package main

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	codexHookProtocolVersion   = "codex-hooks-observer/v2"
	codexHealthProtocolVersion = "codex-hook-bridge-health/v1"
	codexActiveRunLease        = 15 * time.Minute
)

type codexDeliveryHealthFile struct {
	ProtocolVersion   string `json:"protocol_version"`
	ObservedAt        string `json:"observed_at"`
	State             string `json:"state"`
	ReasonCode        string `json:"reason_code"`
	KandevWorkspaceID string `json:"kandev_workspace_id"`
}

func defaultCodexHookSnapshotPath() string {
	if override := strings.TrimSpace(os.Getenv("KANDEV_AGENT_OBSERVER_HOOK_EVENTS")); override != "" {
		return override
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".kandev", "plugins", "ai-delivery-agent-observer", "data", "codex-hook-snapshot.json")
}

func readCodexHookSnapshot(path, workspaceID string, taskScoped bool) ([]nativeAgentSnapshot, []collaborationEdgeSnapshot, []timelineEventSnapshot, bridgeHealthSnapshot) {
	health := bridgeHealthSnapshot{State: "unavailable", Source: "codex_hooks", Error: "尚未收到 Codex 实时 Hook 事件"}
	if taskScoped {
		health.State = "not_applicable"
		health.Error = "任务局部视图不混入外部 Codex 生命周期"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if workspaceID == "" {
		health.State = "workspace_unbound"
		health.Error = "Codex 实时 Hook 未绑定当前 Kandev 工作区"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if path == "" {
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	_, deliveryHealth := readCodexDeliveryHealth(filepath.Join(filepath.Dir(path), "codex-hook-bridge-health.json"), workspaceID)
	if deliveryHealth.State == "out_of_scope" {
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, deliveryHealth
	}
	if deliveryHealth.State == "delivery_failed" {
		// A workspace-bound dispatcher failure is direct channel evidence and
		// remains meaningful even when no lifecycle snapshot was ever created.
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, deliveryHealth
	}
	info, err := os.Lstat(path)
	if err != nil {
		if !os.IsNotExist(err) {
			health.Error = "Codex Hook 快照不可读取"
		}
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if !info.Mode().IsRegular() || info.Size() > maxCodexSnapshotSize {
		health.Error = "Codex Hook 快照不是受支持的普通文件"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	file, err := os.Open(path)
	if err != nil {
		health.Error = "Codex Hook 快照不可读取"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	defer file.Close()
	value := codexSnapshotFile{}
	if err := json.NewDecoder(io.LimitReader(file, maxCodexSnapshotSize+1)).Decode(&value); err != nil {
		health.Error = "Codex Hook 快照格式无效"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if value.ProtocolVersion != codexHookProtocolVersion {
		health.Error = "Codex Hook 快照协议不匹配"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if value.KandevWorkspaceID == "" || value.KandevWorkspaceID != workspaceID {
		health.State = "out_of_scope"
		health.Error = "Codex Hook 工作区绑定缺失或与当前页面不匹配"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	bridgeState := normalizeHookBridgeState(value.Bridge.State)
	health = bridgeHealthSnapshot{
		State:           bridgeState,
		Source:          "codex_hooks",
		ProtocolVersion: codexHookProtocolVersion,
		RootThreadID:    observedID(value.Bridge.RootThreadID, 100),
		LastSuccessAt:   observedTimestamp(value.Bridge.LastSuccessAt),
		GeneratedAt:     observedTimestamp(value.GeneratedAt),
		ActiveRuns:      boundedCount(value.Bridge.ActiveRuns, maxNativeAgents),
		RunCount:        boundedCount(value.Bridge.RunCount, maxNativeAgents),
	}
	if bridgeState == "unavailable" {
		health.Error = "Codex Hook 报告了不支持的通道状态"
	}
	freshRuns := freshActiveRuns(value.Runs, time.Now().UTC())
	health.ActiveRuns = len(freshRuns)
	if bridgeState == "active" && len(freshRuns) == 0 {
		health.State = "stale"
		health.Error = "活跃 Codex 任务已超过 15 分钟没有生命周期或工具事件"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	agents := make([]nativeAgentSnapshot, 0, len(value.Agents))
	for _, raw := range value.Agents {
		agentID := observedID(raw.AgentID, 100)
		parentID := observedID(raw.ParentAgentID, 100)
		rootThreadID := observedID(raw.RootThreadID, 100)
		if agentID == "" || parentID == "" || rootThreadID == "" {
			continue
		}
		state := normalizeHookState(raw.ExecutionState)
		if state == "working" || state == "waiting_on_human" {
			if bridgeState != "active" {
				continue
			}
			if _, active := freshRuns[rootThreadID]; !active {
				continue
			}
		}
		role := observedRole(firstNonEmpty(raw.RoleCN, raw.DisplayName))
		progress, difficulty := hookNarrative(raw.ProgressSummary, raw.CurrentDifficulty, state)
		agents = append(agents, nativeAgentSnapshot{
			AgentID:           agentID,
			ParentAgentID:     parentID,
			RootThreadID:      rootThreadID,
			DisplayName:       role,
			RoleCN:            role,
			Mission:           missionForRole(role),
			ExecutionState:    state,
			ProgressSummary:   progress,
			CurrentDifficulty: difficulty,
			CreatedAt:         observedTimestamp(raw.CreatedAt),
			UpdatedAt:         observedTimestamp(raw.UpdatedAt),
			LastActivityAt:    observedTimestamp(raw.LastActivityAt),
			SourceQuality:     "codex_hooks_realtime",
			Source:            "codex_hooks",
		})
	}
	disambiguateNativeNames(agents)
	edges := sanitizeCodexEdges(value.Edges, "codex_hooks", "codex_hooks_realtime")
	timeline := sanitizeCodexTimeline(value.Timeline, "codex_hooks", "codex_hooks_realtime")
	return agents, edges, timeline, health
}

func readCodexDeliveryHealth(path, workspaceID string) (codexDeliveryHealthFile, bridgeHealthSnapshot) {
	health := bridgeHealthSnapshot{State: "unavailable", Source: "codex_hook_dispatch", Error: "Codex Hook 分发健康证据不可用"}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Size() > 64*1024 {
		return codexDeliveryHealthFile{}, health
	}
	file, err := os.Open(path)
	if err != nil {
		return codexDeliveryHealthFile{}, health
	}
	defer file.Close()
	value := codexDeliveryHealthFile{}
	if err := json.NewDecoder(io.LimitReader(file, 64*1024+1)).Decode(&value); err != nil {
		return codexDeliveryHealthFile{}, health
	}
	if value.ProtocolVersion != codexHealthProtocolVersion {
		health.Error = "Codex Hook 分发健康协议不匹配"
		return value, health
	}
	if value.KandevWorkspaceID == "" || value.KandevWorkspaceID != workspaceID {
		health.State = "out_of_scope"
		health.Error = "Codex Hook 分发通道未绑定当前工作区"
		return value, health
	}
	value.ObservedAt = observedTimestamp(value.ObservedAt)
	if value.State != "ready" {
		health.State = "delivery_failed"
		health.Error = deliveryFailureMessage(value.ReasonCode)
		return value, health
	}
	health.State = "ready"
	health.LastSuccessAt = value.ObservedAt
	return value, health
}

func deliveryFailureMessage(reason string) string {
	messages := map[string]string{
		"workspace_unbound":    "Codex Hook 分发器未绑定 Kandev 工作区",
		"input_too_large":      "Codex Hook 事件超过安全大小限制",
		"receiver_unavailable": "Kandev 实时 Hook 接收器未安装、未启用或校验失败",
		"receiver_failed":      "Kandev 实时 Hook 接收器运行失败",
		"dispatcher_error":     "Codex Hook 分发器运行异常",
	}
	if message := messages[reason]; message != "" {
		return message
	}
	return "Codex Hook 分发失败"
}

func freshActiveRuns(values []codexRunFile, now time.Time) map[string]struct{} {
	result := make(map[string]struct{})
	for _, value := range values {
		root := observedID(value.RootThreadID, 100)
		updatedAt, err := time.Parse(time.RFC3339Nano, observedTimestamp(value.UpdatedAt))
		if root == "" || value.ExecutionState != "active" || err != nil || updatedAt.After(now.Add(5*time.Second)) {
			continue
		}
		if now.Sub(updatedAt) <= codexActiveRunLease {
			result[root] = struct{}{}
		}
	}
	return result
}

func normalizeHookState(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "working":
		return "working"
	case "waiting_on_human":
		return "waiting_on_human"
	case "stopped":
		return "stopped"
	case "finished":
		return "finished"
	case "failed":
		return "failed"
	case "interrupted":
		return "interrupted"
	default:
		return "queued"
	}
}

func normalizeHookBridgeState(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "active":
		return "active"
	case "idle":
		return "idle"
	default:
		return "unavailable"
	}
}

func hookNarrative(rawProgress, rawDifficulty, state string) (string, string) {
	progressAllowlist := map[string]bool{
		"Codex Hook 已确认该 Agent 正在运行": true,
		"Codex Hook 已确认该 Agent 已停止":  true,
		"Codex Hook 已观察到该 Agent":     true,
		"正在运行本地命令或检查":                true,
		"已完成本地命令或检查":                 true,
		"正在等待或继续本地命令":                true,
		"本地命令已返回新结果":                 true,
		"正在修改工程文件":                   true,
		"已完成一项文件修改":                  true,
		"正在写入工程文件":                   true,
		"已完成一项文件写入":                  true,
		"正在更新执行计划":                   true,
		"已完成执行计划更新":                  true,
		"正在检查可视化证据":                  true,
		"已完成可视化证据检查":                 true,
		"正在请求人类确认":                   true,
		"人类确认请求已处理":                  true,
		"正在调用已连接的集成工具":               true,
		"已完成一项集成工具调用":                true,
		"正在使用工程工具":                   true,
		"已完成一项工程工具调用":                true,
		"正在等待权限审批":                   true,
		"主任务会话已结束；该 Agent 不再计入工作中":   true,
		"新的主任务会话已开始；旧运行状态已隔离":        true,
		"Codex Hook 已确认上级中断该 Agent":  true,
	}
	difficultyAllowlist := map[string]bool{
		"Hook 未提供结构化困难字段":                 true,
		"无可验证的困难字段":                       true,
		"未通过隐私最小化 Hook 报告结构化困难":           true,
		"最近一项工程工具调用失败；请在原 Codex 任务核验错误详情": true,
		"继续执行需要人类决定是否授予本次权限":              true,
		"最终工作结果需在原 Codex 任务核验":            true,
		"旧会话未提供可验证的完成结果":                  true,
		"中断原因需在原任务核验":                     true,
	}
	progress, difficulty := nativeNarrative("Codex Hook", state)
	if progressAllowlist[rawProgress] {
		progress = rawProgress
	}
	if difficultyAllowlist[rawDifficulty] {
		difficulty = rawDifficulty
	}
	return progress, difficulty
}

func observedRole(value string) string {
	value = strings.TrimSpace(value)
	for _, definition := range roleDefinitions {
		if definition.Name == value {
			return definition.Name
		}
	}
	return "执行智能体"
}

func observedTimestamp(value string) string {
	value = strings.TrimSpace(value)
	if len(value) > 40 {
		return ""
	}
	if _, err := time.Parse(time.RFC3339Nano, value); err != nil {
		return ""
	}
	return value
}

func sanitizeCodexEdges(values []collaborationEdgeSnapshot, source, quality string) []collaborationEdgeSnapshot {
	summaries := map[string]string{
		"spawn":      "Codex 创建子 Agent",
		"correction": "上级向子 Agent 发送纠偏或补充要求",
		"resume":     "上级恢复子 Agent",
		"close":      "上级关闭或中断子 Agent",
	}
	result := make([]collaborationEdgeSnapshot, 0, len(values))
	for _, value := range values {
		summary, ok := summaries[value.EdgeType]
		if !ok {
			continue
		}
		value.EdgeID = observedID(value.EdgeID, 120)
		value.FromAgentID = observedID(value.FromAgentID, 100)
		value.ToAgentIDs = sanitizeIDs(value.ToAgentIDs)
		if value.EdgeID == "" || value.FromAgentID == "" {
			continue
		}
		value.Action = canonicalAction(value.EdgeType)
		value.Status = observedLifecycleStatus(value.Status)
		value.ObservedAt = observedTimestamp(value.ObservedAt)
		value.Summary = summary
		value.SourceQuality = quality
		value.Source = source
		value.RootThreadID = observedID(value.RootThreadID, 100)
		result = append(result, value)
	}
	return keepLast(result, maxNativeEdges)
}

func sanitizeCodexTimeline(values []timelineEventSnapshot, source, quality string) []timelineEventSnapshot {
	summaries := map[string]string{
		"spawn":         "Codex 确认子 Agent 已开始",
		"correction":    "上级向子 Agent 发送纠偏或补充要求",
		"resume":        "上级恢复子 Agent",
		"wait":          "上级等待子 Agent 结果",
		"close":         "上级关闭或中断子 Agent",
		"activity":      "Codex 记录子 Agent 活动",
		"stopped":       "Codex 确认子 Agent 已停止；结果尚未判定",
		"finished":      "Codex 确认子 Agent 已停止",
		"failed":        "Codex 历史记录显示执行失败",
		"interrupted":   "Codex 历史记录显示执行中断",
		"state":         "Codex 更新子 Agent 状态",
		"permission":    "子 Agent 正在等待权限审批",
		"session_start": "Codex 主任务会话已开始或恢复",
		"session_end":   "Codex 主任务会话已结束",
	}
	result := make([]timelineEventSnapshot, 0, len(values))
	for _, value := range values {
		summary, ok := summaries[value.EventType]
		if !ok {
			continue
		}
		value.EventID = observedID(value.EventID, 120)
		value.ActorAgentID = observedID(value.ActorAgentID, 100)
		value.TargetAgentIDs = sanitizeIDs(value.TargetAgentIDs)
		if value.EventID == "" {
			continue
		}
		value.Status = observedLifecycleStatus(value.Status)
		value.ObservedAt = observedTimestamp(value.ObservedAt)
		value.RootThreadID = observedID(value.RootThreadID, 100)
		value.ActivityKind = observedActivityKind(value.ActivityKind)
		if value.EventType == "activity" {
			summary = hookActivitySummary(value.ActivityKind, value.Status)
		}
		value.Summary = summary
		value.SourceQuality = quality
		value.Source = source
		if value.RepeatCount < 0 || value.RepeatCount > 10000 {
			value.RepeatCount = 0
		}
		result = append(result, value)
	}
	return keepLast(result, maxNativeTimeline)
}

func hookActivitySummary(kind, status string) string {
	labels := map[string]string{
		"command":     "本地命令或检查",
		"file_change": "工程文件修改",
		"plan":        "执行计划更新",
		"evidence":    "可视化证据检查",
		"human_input": "人类确认请求",
		"integration": "集成工具调用",
		"tool":        "工程工具调用",
	}
	label := labels[kind]
	if label == "" {
		label = "工程活动"
	}
	switch observedLifecycleStatus(status) {
	case "started":
		return "子 Agent 开始" + label
	case "failed":
		return "子 Agent 的" + label + "失败"
	default:
		return "子 Agent 完成" + label
	}
}

func canonicalAction(edgeType string) string {
	return map[string]string{
		"spawn": "spawnAgent", "correction": "sendInput", "resume": "resumeAgent", "close": "closeAgent",
	}[edgeType]
}

func observedLifecycleStatus(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "started", "starting", "running", "working", "inprogress", "in_progress":
		return "started"
	case "completed", "complete", "finished":
		return "completed"
	case "stopped":
		return "stopped"
	case "failed", "fail", "error", "errored":
		return "failed"
	case "interrupted", "cancelled", "canceled", "closed":
		return "interrupted"
	case "waiting", "waiting_on_human", "permission_required":
		return "waiting"
	default:
		return "recorded"
	}
}
