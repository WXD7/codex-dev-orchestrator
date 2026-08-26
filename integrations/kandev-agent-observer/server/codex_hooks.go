package main

import (
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const codexHookProtocolVersion = "codex-hooks-observer/v1"

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
	if path == "" {
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
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
	if workspaceID != "" && value.KandevWorkspaceID != "" && value.KandevWorkspaceID != workspaceID {
		health.State = "out_of_scope"
		health.Error = "Codex Hook 当前绑定到另一个 Kandev 工作区"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	health = bridgeHealthSnapshot{
		State:           "ready",
		Source:          "codex_hooks",
		ProtocolVersion: codexHookProtocolVersion,
		RootThreadID:    observedID(value.Bridge.RootThreadID, 100),
		LastSuccessAt:   observedTimestamp(value.Bridge.LastSuccessAt),
		GeneratedAt:     observedTimestamp(value.GeneratedAt),
	}
	agents := make([]nativeAgentSnapshot, 0, len(value.Agents))
	for _, raw := range value.Agents {
		agentID := observedID(raw.AgentID, 100)
		parentID := observedID(raw.ParentAgentID, 100)
		if agentID == "" || parentID == "" {
			continue
		}
		state := normalizeHookState(raw.ExecutionState)
		role := observedRole(firstNonEmpty(raw.RoleCN, raw.DisplayName))
		progress, difficulty := nativeNarrative("Codex Hook", state)
		agents = append(agents, nativeAgentSnapshot{
			AgentID:           agentID,
			ParentAgentID:     parentID,
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

func normalizeHookState(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "working":
		return "working"
	case "stopped":
		return "stopped"
	case "finished":
		return "finished"
	case "interrupted":
		return "interrupted"
	default:
		return "queued"
	}
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
		result = append(result, value)
	}
	return keepLast(result, maxNativeEdges)
}

func sanitizeCodexTimeline(values []timelineEventSnapshot, source, quality string) []timelineEventSnapshot {
	summaries := map[string]string{
		"spawn":       "Codex 确认子 Agent 已开始",
		"correction":  "上级向子 Agent 发送纠偏或补充要求",
		"resume":      "上级恢复子 Agent",
		"wait":        "上级等待子 Agent 结果",
		"close":       "上级关闭或中断子 Agent",
		"activity":    "Codex 记录子 Agent 活动",
		"stopped":     "Codex 确认子 Agent 已停止；结果尚未判定",
		"finished":    "Codex 确认子 Agent 已停止",
		"failed":      "Codex 历史记录显示执行失败",
		"interrupted": "Codex 历史记录显示执行中断",
		"state":       "Codex 更新子 Agent 状态",
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
	default:
		return "recorded"
	}
}
