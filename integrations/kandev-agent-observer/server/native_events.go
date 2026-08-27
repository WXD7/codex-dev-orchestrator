package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/kandev/kandev/pkg/pluginsdk"
)

const (
	nativeStateKey       = "native-agent-events-v1"
	maxNativeAgents      = 128
	maxNativeEdges       = 256
	maxNativeTimeline    = 512
	maxCodexSnapshotSize = 2 << 20
	bridgeStaleAfter     = 30 * time.Second
)

var (
	secretAssignmentPattern = regexp.MustCompile(`(?i)(api[_ -]?key|access[_ -]?token|auth[_ -]?token|token|password|secret|密钥|密码)\s*(?::|=|is\b|是|为)\s*["']?[^,;，；\n\r]{1,240}`)
	bearerPattern           = regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*`)
	directTokenPattern      = regexp.MustCompile(`\b(?:sk|ds)-[A-Za-z0-9_-]{8,}\b`)
	observedIDPattern       = regexp.MustCompile(`^[A-Za-z0-9/][A-Za-z0-9._:/-]{0,127}$`)
)

type eventStore struct {
	mu sync.Mutex
}

type nativeAgentSnapshot struct {
	AgentID           string `json:"agent_id"`
	ParentAgentID     string `json:"parent_agent_id,omitempty"`
	RootThreadID      string `json:"root_thread_id,omitempty"`
	DisplayName       string `json:"display_name"`
	RoleCN            string `json:"role_cn"`
	Mission           string `json:"mission"`
	ExecutionState    string `json:"execution_state"`
	ProgressSummary   string `json:"progress_summary"`
	CurrentDifficulty string `json:"current_difficulty"`
	TaskID            string `json:"task_id,omitempty"`
	SessionID         string `json:"session_id,omitempty"`
	WorkspaceID       string `json:"workspace_id,omitempty"`
	CreatedAt         string `json:"created_at,omitempty"`
	UpdatedAt         string `json:"updated_at,omitempty"`
	LastActivityAt    string `json:"last_activity_at,omitempty"`
	SourceQuality     string `json:"source_quality"`
	Source            string `json:"source"`
}

type collaborationEdgeSnapshot struct {
	EdgeID        string   `json:"edge_id"`
	EdgeType      string   `json:"edge_type"`
	Action        string   `json:"action,omitempty"`
	FromAgentID   string   `json:"from_agent_id,omitempty"`
	ToAgentIDs    []string `json:"to_agent_ids"`
	Status        string   `json:"status,omitempty"`
	ObservedAt    string   `json:"observed_at,omitempty"`
	Summary       string   `json:"summary"`
	SourceQuality string   `json:"source_quality"`
	Source        string   `json:"source"`
	WorkspaceID   string   `json:"workspace_id,omitempty"`
	TaskID        string   `json:"task_id,omitempty"`
	RootThreadID  string   `json:"root_thread_id,omitempty"`
}

type timelineEventSnapshot struct {
	EventID        string   `json:"event_id"`
	EventType      string   `json:"event_type"`
	ActorAgentID   string   `json:"actor_agent_id,omitempty"`
	TargetAgentIDs []string `json:"target_agent_ids"`
	Status         string   `json:"status,omitempty"`
	ObservedAt     string   `json:"observed_at,omitempty"`
	Summary        string   `json:"summary"`
	SourceQuality  string   `json:"source_quality"`
	Source         string   `json:"source"`
	WorkspaceID    string   `json:"workspace_id,omitempty"`
	TaskID         string   `json:"task_id,omitempty"`
	RootThreadID   string   `json:"root_thread_id,omitempty"`
	ActivityKind   string   `json:"activity_kind,omitempty"`
	RepeatCount    int      `json:"repeat_count,omitempty"`
}

type bridgeHealthSnapshot struct {
	State           string `json:"state"`
	Source          string `json:"source"`
	ProtocolVersion string `json:"protocol_version,omitempty"`
	RootThreadID    string `json:"root_thread_id,omitempty"`
	LastSuccessAt   string `json:"last_success_at,omitempty"`
	GeneratedAt     string `json:"generated_at,omitempty"`
	Error           string `json:"error,omitempty"`
	ActiveRuns      int    `json:"active_runs,omitempty"`
	RunCount        int    `json:"run_count,omitempty"`
}

type storedNativeState struct {
	Agents   []nativeAgentSnapshot       `json:"agents"`
	Edges    []collaborationEdgeSnapshot `json:"edges"`
	Timeline []timelineEventSnapshot     `json:"timeline"`
	Seen     []string                    `json:"seen"`
}

type projectedNativeEvent struct {
	Agent nativeAgentSnapshot
	Edge  *collaborationEdgeSnapshot
	Event timelineEventSnapshot
}

func (p *observerPlugin) OnEvent(ctx context.Context, event *pluginsdk.Event) error {
	projection, ok := projectKandevNativeEvent(event)
	if !ok {
		return nil
	}
	host := p.Host()
	if host == nil {
		return errors.New("Kandev Host 尚未就绪")
	}
	if projection.Agent.WorkspaceID == "" && projection.Agent.TaskID != "" {
		task, err := host.Tasks().Get(ctx, projection.Agent.TaskID)
		if err != nil {
			return fmt.Errorf("resolve event workspace: %w", err)
		}
		if task != nil {
			projection.Agent.WorkspaceID = task.WorkspaceID
			projection.Event.WorkspaceID = task.WorkspaceID
			if projection.Edge != nil {
				projection.Edge.WorkspaceID = task.WorkspaceID
			}
		}
	}

	p.events.mu.Lock()
	defer p.events.mu.Unlock()
	value, found, err := host.GetState(ctx, "instance", "", nativeStateKey)
	if err != nil {
		return fmt.Errorf("load native event state: %w", err)
	}
	state := storedNativeState{}
	if found {
		if err := remarshal(value, &state); err != nil {
			return fmt.Errorf("decode native event state: %w", err)
		}
	}
	if containsString(state.Seen, projection.Event.EventID) {
		return nil
	}
	state.Seen = keepLast(append(state.Seen, projection.Event.EventID), maxNativeTimeline)
	state.Agents = upsertNativeAgent(state.Agents, projection.Agent)
	if projection.Edge != nil {
		state.Edges = keepLast(append(state.Edges, *projection.Edge), maxNativeEdges)
	}
	state.Timeline = keepLast(append(state.Timeline, projection.Event), maxNativeTimeline)
	encoded, err := structMap(state)
	if err != nil {
		return fmt.Errorf("encode native event state: %w", err)
	}
	return host.SetState(ctx, "instance", "", nativeStateKey, encoded)
}

func projectKandevNativeEvent(event *pluginsdk.Event) (projectedNativeEvent, bool) {
	if event == nil || (event.EventType != "agent.stream" && !strings.HasPrefix(event.EventType, "agent.stream.")) {
		return projectedNativeEvent{}, false
	}
	payload := event.Payload
	data, ok := mapValue(payload, "data")
	if !ok {
		return projectedNativeEvent{}, false
	}
	normalized, ok := mapValue(data, "normalized")
	if !ok || stringValue(normalized, "kind") != "subagent_task" {
		return projectedNativeEvent{}, false
	}
	subagent, ok := mapValue(normalized, "subagent_task")
	if !ok {
		return projectedNativeEvent{}, false
	}
	childID := firstNonEmpty(stringValue(subagent, "child_session_id"), stringValue(subagent, "agent_id"))
	if childID == "" {
		return projectedNativeEvent{}, false
	}
	parentID := firstNonEmpty(stringValue(data, "acp_session_id"), stringValue(payload, "session_id"))
	status := firstNonEmpty(stringValue(subagent, "status"), stringValue(data, "tool_status"), "started")
	state := normalizeNativeState(status)
	description := strings.Join([]string{
		stringValue(subagent, "subagent_type"),
		stringValue(subagent, "description"),
	}, " ")
	role := nativeRole(description)
	observedAt := observedTimestamp(firstNonEmpty(event.OccurredAt, stringValue(payload, "timestamp")))
	if observedAt == "" {
		observedAt = time.Now().UTC().Format(time.RFC3339Nano)
	}
	taskID := observedID(stringValue(payload, "task_id"), 100)
	sessionID := observedID(stringValue(payload, "session_id"), 100)
	eventID := observedID(event.EventID, 120)
	if eventID == "" {
		eventID = observedID(stringValue(data, "tool_call_id")+":"+childID+":"+state, 120)
	}
	cleanChildID := observedID(childID, 100)
	cleanParentID := observedID(parentID, 100)
	if cleanChildID == "" || cleanParentID == "" || eventID == "" {
		return projectedNativeEvent{}, false
	}
	progress, difficulty := nativeNarrative("Kandev", state)
	agent := nativeAgentSnapshot{
		AgentID:           cleanChildID,
		ParentAgentID:     cleanParentID,
		RootThreadID:      sessionID,
		DisplayName:       role.Name,
		RoleCN:            role.Name,
		Mission:           role.Mission,
		ExecutionState:    state,
		ProgressSummary:   progress,
		CurrentDifficulty: difficulty,
		TaskID:            taskID,
		SessionID:         sessionID,
		WorkspaceID:       observedID(event.WorkspaceID, 100),
		UpdatedAt:         observedAt,
		LastActivityAt:    observedAt,
		SourceQuality:     "kandev_agent_stream_exact",
		Source:            "kandev_agent_stream",
	}
	eventType := "state"
	summary := "Kandev 更新了子 Agent 状态"
	var edge *collaborationEdgeSnapshot
	if state == "working" {
		eventType = "spawn"
		summary = "Kandev 确认子 Agent 已开始工作"
		edge = &collaborationEdgeSnapshot{
			EdgeID: eventID + ":spawn", EdgeType: "spawn", Action: "spawnAgent",
			FromAgentID: agent.ParentAgentID, ToAgentIDs: []string{agent.AgentID}, Status: observedLifecycleStatus(status),
			ObservedAt: observedAt, Summary: "Kandev 创建子 Agent", SourceQuality: agent.SourceQuality,
			Source: agent.Source, WorkspaceID: agent.WorkspaceID, TaskID: agent.TaskID, RootThreadID: agent.RootThreadID,
		}
	} else if state == "finished" || state == "failed" || state == "interrupted" {
		eventType = state
		summary = "Kandev 确认子 Agent 已" + map[string]string{"finished": "完成", "failed": "失败", "interrupted": "中断"}[state]
	}
	return projectedNativeEvent{
		Agent: agent,
		Edge:  edge,
		Event: timelineEventSnapshot{
			EventID: eventID, EventType: eventType, ActorAgentID: agent.ParentAgentID,
			TargetAgentIDs: []string{agent.AgentID}, Status: observedLifecycleStatus(status), ObservedAt: observedAt,
			Summary: summary, SourceQuality: agent.SourceQuality, Source: agent.Source,
			WorkspaceID: agent.WorkspaceID, TaskID: agent.TaskID, RootThreadID: agent.RootThreadID,
		},
	}, true
}

func loadKandevNativeState(ctx context.Context, host pluginsdk.Host, workspaceID, taskID string) ([]nativeAgentSnapshot, []collaborationEdgeSnapshot, []timelineEventSnapshot, error) {
	value, found, err := host.GetState(ctx, "instance", "", nativeStateKey)
	if err != nil || !found {
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, err
	}
	state := storedNativeState{}
	if err := remarshal(value, &state); err != nil {
		return nil, nil, nil, err
	}
	agents := filterNativeAgents(state.Agents, workspaceID, taskID)
	edges := filterNativeEdges(state.Edges, workspaceID, taskID)
	timeline := filterNativeTimeline(state.Timeline, workspaceID, taskID)
	disambiguateNativeNames(agents)
	return agents, edges, timeline, nil
}

type codexSnapshotFile struct {
	ProtocolVersion   string                      `json:"protocol_version"`
	GeneratedAt       string                      `json:"generated_at"`
	KandevWorkspaceID string                      `json:"kandev_workspace_id"`
	Bridge            codexBridgeFile             `json:"bridge"`
	Agents            []codexAgentFile            `json:"agents"`
	Edges             []collaborationEdgeSnapshot `json:"edges"`
	Timeline          []timelineEventSnapshot     `json:"timeline"`
	Runs              []codexRunFile              `json:"runs"`
}

type codexBridgeFile struct {
	State         string `json:"state"`
	Source        string `json:"source"`
	RootThreadID  string `json:"root_thread_id"`
	LastSuccessAt string `json:"last_success_at"`
	Error         string `json:"error"`
	ActiveRuns    int    `json:"active_runs"`
	RunCount      int    `json:"run_count"`
}

type codexRunFile struct {
	RootThreadID   string `json:"root_thread_id"`
	ExecutionState string `json:"execution_state"`
	StartedAt      string `json:"started_at"`
	UpdatedAt      string `json:"updated_at"`
	EndedAt        string `json:"ended_at"`
}

type codexAgentFile struct {
	AgentID           string `json:"agent_id"`
	ParentAgentID     string `json:"parent_agent_id"`
	RootThreadID      string `json:"root_thread_id"`
	DisplayName       string `json:"display_name"`
	RoleCN            string `json:"role_cn"`
	ExecutionState    string `json:"execution_state"`
	ProgressSummary   string `json:"progress_summary"`
	CurrentDifficulty string `json:"current_difficulty"`
	CreatedAt         string `json:"created_at"`
	UpdatedAt         string `json:"updated_at"`
	LastActivityAt    string `json:"last_activity_at"`
	SourceQuality     string `json:"source_quality"`
	Source            string `json:"source"`
}

func defaultCodexSnapshotPath() string {
	if override := strings.TrimSpace(os.Getenv("KANDEV_AGENT_OBSERVER_EVENTS")); override != "" {
		return override
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return filepath.Join(home, ".kandev", "plugins", "ai-delivery-agent-observer", "data", "codex-app-snapshot.json")
}

func readCodexAppSnapshot(path, workspaceID string, taskScoped bool) ([]nativeAgentSnapshot, []collaborationEdgeSnapshot, []timelineEventSnapshot, bridgeHealthSnapshot) {
	health := bridgeHealthSnapshot{State: "unavailable", Source: "codex_app_server_history", Error: "Codex 持久化历史尚未同步"}
	if taskScoped {
		health.State = "not_applicable"
		health.Error = "任务局部视图不混入外部 Codex 任务树"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if path == "" {
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	info, err := os.Lstat(path)
	if err != nil {
		if !os.IsNotExist(err) {
			health.Error = sanitizeObserved(err.Error(), 180)
		}
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if !info.Mode().IsRegular() {
		health.Error = "Codex 桥快照不是普通文件"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	file, err := os.Open(path)
	if err != nil {
		health.Error = sanitizeObserved(err.Error(), 180)
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	defer file.Close()
	decoder := json.NewDecoder(io.LimitReader(file, maxCodexSnapshotSize+1))
	value := codexSnapshotFile{}
	if err := decoder.Decode(&value); err != nil {
		health.Error = "Codex 桥快照格式无效"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if workspaceID != "" && value.KandevWorkspaceID != "" && value.KandevWorkspaceID != workspaceID {
		health.State = "out_of_scope"
		health.Error = "Codex 桥当前绑定到另一个 Kandev 工作区"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	if value.ProtocolVersion != "codex-app-server-history/v1" {
		health.Error = "Codex 历史快照协议不匹配"
		return []nativeAgentSnapshot{}, []collaborationEdgeSnapshot{}, []timelineEventSnapshot{}, health
	}
	health = bridgeHealthSnapshot{
		State: "history_synced", Source: "codex_app_server_history",
		ProtocolVersion: sanitizeObserved(value.ProtocolVersion, 80), RootThreadID: observedID(value.Bridge.RootThreadID, 100),
		LastSuccessAt: sanitizeObserved(value.Bridge.LastSuccessAt, 60), GeneratedAt: sanitizeObserved(value.GeneratedAt, 60),
		Error: sanitizeObserved(value.Bridge.Error, 180), ActiveRuns: boundedCount(value.Bridge.ActiveRuns, maxNativeAgents),
		RunCount: boundedCount(value.Bridge.RunCount, maxNativeAgents),
	}
	if value.Bridge.State != "history_synced" {
		health.State = "stale"
		health.Error = "Codex 持久化历史同步器报告异常"
	} else if health.State == "history_synced" {
		if timestamp, err := time.Parse(time.RFC3339Nano, health.LastSuccessAt); err != nil || time.Since(timestamp) > bridgeStaleAfter {
			health.State = "stale"
			health.Error = "Codex 持久化历史已超过 30 秒未同步"
		}
	}
	agents := make([]nativeAgentSnapshot, 0, len(value.Agents))
	for _, raw := range value.Agents {
		role := observedRole(firstNonEmpty(raw.RoleCN, raw.DisplayName))
		progress, difficulty := nativeNarrative("Codex 历史", "historical")
		agents = append(agents, nativeAgentSnapshot{
			AgentID: observedID(raw.AgentID, 100), ParentAgentID: observedID(raw.ParentAgentID, 100),
			RootThreadID: firstNonEmpty(observedID(raw.RootThreadID, 100), observedID(value.Bridge.RootThreadID, 100)),
			DisplayName:  sanitizeObserved(firstNonEmpty(raw.DisplayName, role), 32), RoleCN: role,
			Mission: missionForRole(role), ExecutionState: "historical",
			ProgressSummary: progress, CurrentDifficulty: difficulty,
			CreatedAt: observedTimestamp(raw.CreatedAt), UpdatedAt: observedTimestamp(raw.UpdatedAt),
			LastActivityAt: observedTimestamp(raw.LastActivityAt), SourceQuality: "codex_app_server_history", Source: "codex_app_server_history",
		})
	}
	disambiguateNativeNames(agents)
	edges := sanitizeCodexEdges(value.Edges, "codex_app_server_history", "codex_app_server_history")
	for index := range edges {
		if edges[index].RootThreadID == "" {
			edges[index].RootThreadID = observedID(value.Bridge.RootThreadID, 100)
		}
	}
	knownSpawn := make(map[string]bool)
	for _, edge := range edges {
		if edge.EdgeType == "spawn" {
			for _, target := range edge.ToAgentIDs {
				knownSpawn[target] = true
			}
		}
	}
	for _, agent := range agents {
		if agent.AgentID == "" || agent.ParentAgentID == "" || knownSpawn[agent.AgentID] {
			continue
		}
		edges = append(edges, collaborationEdgeSnapshot{
			EdgeID: "parent:" + agent.AgentID, EdgeType: "spawn", Action: "parentThreadId",
			FromAgentID: agent.ParentAgentID, ToAgentIDs: []string{agent.AgentID}, Status: agent.ExecutionState,
			ObservedAt: agent.CreatedAt, Summary: "Codex 记录的父子 Agent 关系",
			SourceQuality: "codex_app_server_history", Source: "codex_app_server_history", RootThreadID: agent.RootThreadID,
		})
	}
	timeline := sanitizeCodexTimeline(value.Timeline, "codex_app_server_history", "codex_app_server_history")
	for index := range timeline {
		if timeline[index].RootThreadID == "" {
			timeline[index].RootThreadID = observedID(value.Bridge.RootThreadID, 100)
		}
	}
	return agents, edges, timeline, health
}

func sanitizeEdges(values []collaborationEdgeSnapshot, source string) []collaborationEdgeSnapshot {
	result := make([]collaborationEdgeSnapshot, 0, len(values))
	for _, value := range values {
		value.EdgeID = observedID(value.EdgeID, 120)
		value.EdgeType = sanitizeObserved(value.EdgeType, 32)
		value.Action = sanitizeObserved(value.Action, 40)
		value.FromAgentID = observedID(value.FromAgentID, 100)
		value.ToAgentIDs = sanitizeIDs(value.ToAgentIDs)
		value.Status = sanitizeObserved(value.Status, 40)
		value.ObservedAt = sanitizeObserved(value.ObservedAt, 60)
		value.Summary = sanitizeObserved(value.Summary, 180)
		value.SourceQuality = "codex_app_server_exact"
		value.Source = source
		value.RootThreadID = observedID(value.RootThreadID, 100)
		result = append(result, value)
	}
	return keepLast(result, maxNativeEdges)
}

func sanitizeTimeline(values []timelineEventSnapshot, source string) []timelineEventSnapshot {
	result := make([]timelineEventSnapshot, 0, len(values))
	for _, value := range values {
		value.EventID = observedID(value.EventID, 120)
		value.EventType = sanitizeObserved(value.EventType, 32)
		value.ActorAgentID = observedID(value.ActorAgentID, 100)
		value.TargetAgentIDs = sanitizeIDs(value.TargetAgentIDs)
		value.Status = sanitizeObserved(value.Status, 40)
		value.ObservedAt = sanitizeObserved(value.ObservedAt, 60)
		value.Summary = sanitizeObserved(value.Summary, 180)
		value.SourceQuality = "codex_app_server_exact"
		value.Source = source
		value.RootThreadID = observedID(value.RootThreadID, 100)
		value.ActivityKind = observedActivityKind(value.ActivityKind)
		result = append(result, value)
	}
	return keepLast(result, maxNativeTimeline)
}

func sanitizeIDs(values []string) []string {
	result := make([]string, 0, len(values))
	for _, value := range values {
		if cleaned := observedID(value, 100); cleaned != "" {
			result = append(result, cleaned)
		}
	}
	return result
}

func filterNativeAgents(values []nativeAgentSnapshot, workspaceID, taskID string) []nativeAgentSnapshot {
	result := make([]nativeAgentSnapshot, 0, len(values))
	for _, value := range values {
		if workspaceID != "" && value.WorkspaceID != workspaceID {
			continue
		}
		if taskID != "" && value.TaskID != taskID {
			continue
		}
		result = append(result, value)
	}
	sort.SliceStable(result, func(i, j int) bool { return result[i].UpdatedAt < result[j].UpdatedAt })
	return result
}

func filterNativeEdges(values []collaborationEdgeSnapshot, workspaceID, taskID string) []collaborationEdgeSnapshot {
	result := make([]collaborationEdgeSnapshot, 0, len(values))
	for _, value := range values {
		if workspaceID != "" && value.WorkspaceID != workspaceID {
			continue
		}
		if taskID != "" && value.TaskID != taskID {
			continue
		}
		result = append(result, value)
	}
	return result
}

func filterNativeTimeline(values []timelineEventSnapshot, workspaceID, taskID string) []timelineEventSnapshot {
	result := make([]timelineEventSnapshot, 0, len(values))
	for _, value := range values {
		if workspaceID != "" && value.WorkspaceID != workspaceID {
			continue
		}
		if taskID != "" && value.TaskID != taskID {
			continue
		}
		result = append(result, value)
	}
	return result
}

func upsertNativeAgent(values []nativeAgentSnapshot, incoming nativeAgentSnapshot) []nativeAgentSnapshot {
	for index := range values {
		if values[index].AgentID != incoming.AgentID {
			continue
		}
		incoming.CreatedAt = firstNonEmpty(values[index].CreatedAt, incoming.UpdatedAt)
		values[index] = incoming
		return values
	}
	incoming.CreatedAt = firstNonEmpty(incoming.CreatedAt, incoming.UpdatedAt)
	return keepLast(append(values, incoming), maxNativeAgents)
}

func disambiguateNativeNames(values []nativeAgentSnapshot) {
	counts := make(map[string]int)
	for index := range values {
		base := firstNonEmpty(values[index].RoleCN, values[index].DisplayName, "执行智能体")
		key := values[index].RootThreadID + "|" + base
		counts[key]++
		values[index].DisplayName = base
		if counts[key] > 1 {
			values[index].DisplayName = fmt.Sprintf("%s %d", base, counts[key])
		}
	}
}

func boundedCount(value, maximum int) int {
	if value < 0 {
		return 0
	}
	if value > maximum {
		return maximum
	}
	return value
}

func observedActivityKind(value string) string {
	normalized := strings.ToLower(strings.TrimSpace(value))
	switch normalized {
	case "command", "file_change", "plan", "evidence", "human_input", "integration", "tool":
		return normalized
	default:
		return ""
	}
}

func nativeRole(value string) roleDefinition {
	lower := strings.ToLower(value)
	for _, definition := range roleDefinitions {
		if strings.Contains(value, definition.Name) {
			return definition
		}
		for _, key := range definition.Keys {
			if strings.Contains(lower, strings.ToLower(key)) {
				return definition
			}
		}
	}
	return roleDefinition{Name: "执行智能体", Mission: "执行分配的工作并报告可验证状态"}
}

func missionForRole(role string) string {
	for _, definition := range roleDefinitions {
		if definition.Name == role {
			return definition.Mission
		}
	}
	return "执行分配的工作并报告可验证状态"
}

func nativeNarrative(source, state string) (string, string) {
	switch state {
	case "working":
		return source + " 已确认该 Agent 正在工作", "未通过结构化事件报告"
	case "finished":
		return source + " 已确认该 Agent 完成", "无已报告困难"
	case "stopped":
		return source + " 已确认该 Agent 停止；结果尚未判定", "需在原任务核验是否成功"
	case "failed":
		return source + " 已确认该 Agent 失败", "需检查原会话中的失败证据"
	case "interrupted":
		return source + " 已确认该 Agent 被中断", "中断原因需在原会话核验"
	case "waiting_on_dependency":
		return source + " 已确认该 Agent 正在等待", "等待上级或依赖"
	case "waiting_on_human":
		return source + " 已确认该 Agent 正在等待人类处理", "继续执行需要人类确认"
	case "historical":
		return source + "仅用于补全持久化任务树", "历史通道不判断当前困难"
	default:
		return source + " 已观察到该 Agent", "未通过结构化事件报告"
	}
}

func normalizeNativeState(value string) string {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "active", "running", "working", "inprogress", "in_progress", "started", "starting":
		return "working"
	case "completed", "complete", "finished", "idle":
		return "finished"
	case "failed", "fail", "error", "errored", "systemerror":
		return "failed"
	case "interrupted", "cancelled", "canceled", "shutdown", "stopped", "notfound":
		return "interrupted"
	case "wait", "waiting":
		return "waiting_on_dependency"
	case "queued", "pending", "not_loaded", "notloaded":
		return "queued"
	case "historical", "history":
		return "historical"
	default:
		return "queued"
	}
}

func sanitizeObserved(value string, limit int) string {
	value = strings.Join(strings.Fields(value), " ")
	value = secretAssignmentPattern.ReplaceAllString(value, "$1=[已脱敏]")
	value = directTokenPattern.ReplaceAllString(value, "[已脱敏]")
	value = bearerPattern.ReplaceAllString(value, "Bearer [已脱敏]")
	return cleanField(value, limit)
}

func observedID(value string, limit int) string {
	value = strings.TrimSpace(value)
	if len(value) > limit || !observedIDPattern.MatchString(value) {
		return ""
	}
	return value
}

func mapValue(value map[string]any, key string) (map[string]any, bool) {
	result, ok := value[key].(map[string]any)
	return result, ok
}

func stringValue(value map[string]any, key string) string {
	result, _ := value[key].(string)
	return result
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		if strings.TrimSpace(value) != "" {
			return value
		}
	}
	return ""
}

func containsString(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func keepLast[T any](values []T, limit int) []T {
	if len(values) <= limit {
		return values
	}
	return append([]T(nil), values[len(values)-limit:]...)
}

func remarshal(value any, output any) error {
	encoded, err := json.Marshal(value)
	if err != nil {
		return err
	}
	return json.Unmarshal(encoded, output)
}

func structMap(value any) (map[string]any, error) {
	result := map[string]any{}
	if err := remarshal(value, &result); err != nil {
		return nil, err
	}
	return result, nil
}
