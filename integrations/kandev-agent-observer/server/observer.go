package main

import (
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"regexp"
	"sort"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/kandev/kandev/pkg/pluginsdk"
)

const (
	snapshotWebhookKey = "snapshot"
	maxTasks           = 200
	maxSessions        = 300
	maxMessages        = 600
	pageLimit          = int32(200)
	maxPages           = 3
)

var heartbeatPattern = regexp.MustCompile(`(?s)\[\s*([^\]\n｜|]{1,32})\s*[｜|]\s*(working|waiting|finished|queued|starting|failed|stopped)\s*\]\s*进展\s*[:：]\s*(.*?)\s*[｜|]\s*当前困难\s*[:：]\s*(.*?)\s*[｜|]\s*依赖\s*[:：]\s*(.*?)\s*[｜|]\s*需人处理\s*[:：]\s*(是|否|yes|no)\s*[｜|]\s*下一步\s*[:：]\s*(.*)`)

type observerPlugin struct {
	pluginsdk.UnimplementedPlugin
	events eventStore
}

type snapshot struct {
	GeneratedAt        string                      `json:"generated_at"`
	WorkspaceID        string                      `json:"workspace_id,omitempty"`
	Summary            snapshotSummary             `json:"summary"`
	Agents             []agentSnapshot             `json:"agents"`
	KandevNativeAgents []nativeAgentSnapshot       `json:"kandev_native_agents"`
	CodexNativeAgents  []nativeAgentSnapshot       `json:"codex_native_agents"`
	CodexHistoryAgents []nativeAgentSnapshot       `json:"codex_history_agents"`
	Edges              []collaborationEdgeSnapshot `json:"edges"`
	Timeline           []timelineEventSnapshot     `json:"timeline"`
	Bridge             bridgeHealthSnapshot        `json:"bridge"`
	HistoryBridge      bridgeHealthSnapshot        `json:"history_bridge"`
}

type snapshotSummary struct {
	TotalTasks          int `json:"total_tasks"`
	UniqueRoles         int `json:"unique_roles"`
	Working             int `json:"working"`
	Queued              int `json:"queued"`
	WaitingOnHuman      int `json:"waiting_on_human"`
	WaitingOnDependency int `json:"waiting_on_dependency"`
	Finished            int `json:"finished"`
	Failed              int `json:"failed"`
	Stale               int `json:"stale"`
	KandevNative        int `json:"kandev_native"`
	CodexNative         int `json:"codex_native"`
	CodexHistory        int `json:"codex_history"`
	InferredTasks       int `json:"inferred_tasks"`
	Corrections         int `json:"corrections"`
	TimelineEvents      int `json:"timeline_events"`
}

type agentSnapshot struct {
	AgentID           string         `json:"agent_id"`
	DisplayName       string         `json:"display_name"`
	Mission           string         `json:"mission"`
	TaskTitle         string         `json:"task_title"`
	ExecutionState    string         `json:"execution_state"`
	DeliveryVerdict   string         `json:"delivery_verdict"`
	ProgressSummary   string         `json:"progress_summary"`
	CurrentDifficulty string         `json:"current_difficulty"`
	Dependency        string         `json:"dependency"`
	NeedsHuman        bool           `json:"needs_human"`
	NextStep          string         `json:"next_step"`
	LastHeartbeatAt   string         `json:"last_heartbeat_at,omitempty"`
	HeartbeatHealth   string         `json:"heartbeat_health"`
	SourceQuality     string         `json:"source_quality"`
	Source            snapshotSource `json:"source"`
	Model             string         `json:"model,omitempty"`
	KandevTaskState   string         `json:"kandev_task_state"`
	SessionCount      int            `json:"session_count"`
}

type snapshotSource struct {
	TaskID    string `json:"task_id"`
	SessionID string `json:"session_id,omitempty"`
}

type parsedHeartbeat struct {
	Role       string
	State      string
	Progress   string
	Difficulty string
	Dependency string
	NeedsHuman bool
	NextStep   string
	CreatedAt  string
	SessionID  string
}

type roleDefinition struct {
	Name    string
	Mission string
	Keys    []string
}

var roleDefinitions = []roleDefinition{
	{Name: "最终盲验裁决", Mission: "独立检查证据包并给出最终交付裁决", Keys: []string{"consolidated-packet-final", "post-repair verifier", "independent blind", "final verifier", "final adjudication"}},
	{Name: "意图确认员", Mission: "在开发前确认目标、边界、技术偏好和可验收结果", Keys: []string{"intent confirmation", "intent-confirmation", "requirement clarification", "意图确认", "需求确认"}},
	{Name: "意图检查员", Mission: "持续检查实现是否偏离已确认的人类意图", Keys: []string{"intent checker", "intent-check", "intent conformance", "意图检查", "需求检查"}},
	{Name: "技术调研员", Mission: "调研社区、学术与开源路线并形成可比较证据", Keys: []string{"technical research", "tech-research", "research scout", "技术调研", "框架比较", "技术探索"}},
	{Name: "独立审查员", Mission: "独立检查实现与证据并报告可复现问题", Keys: []string{"reviewer", "critic", "audit", "judge"}},
	{Name: "测试验证员", Mission: "运行确定性测试并核验关键行为", Keys: []string{"tester", "qa", "verification"}},
	{Name: "安全审查员", Mission: "检查隐私、密钥、权限与安全边界", Keys: []string{"security auditor", "privacy auditor"}},
	{Name: "架构设计员", Mission: "检查架构边界、集成路径与演进风险", Keys: []string{"architect", "architecture designer"}},
	{Name: "协同调度员", Mission: "协调 Agent 生命周期、依赖、纠偏与收敛", Keys: []string{"coordinator", "orchestrator", "supervisor"}},
	{Name: "实现智能体", Mission: "执行分配的实现工作并报告可验证状态", Keys: []string{"implementer", "developer", "coder", "worker"}},
	{Name: "调研质检员", Mission: "核验调研的新颖性、来源质量与适配结论", Keys: []string{"research quality", "research-quality"}},
	{Name: "统一赛马评测员", Mission: "用统一数据与指标比较候选技术路线", Keys: []string{"race evaluator", "race-evaluator", "benchmark evaluator", "赛马评测", "方案比较"}},
	{Name: "技术路线裁决", Mission: "依据统一评测选择、融合或淘汰技术路线", Keys: []string{"route adjudication", "route-adjudication", "architecture decision"}},
	{Name: "需求语义审查", Mission: "核对需求、交付合同与实现语义的一致性", Keys: []string{"requirement-conformance", "contract-semantics", "requirement semantics"}},
	{Name: "信任边界审查", Mission: "检查权限、证据来源与人机责任边界", Keys: []string{"trust-boundary", "trust boundary"}},
	{Name: "安全边界审查", Mission: "检查不可逆操作、权限升级和安全边界", Keys: []string{"safety-boundary-conformance", "safety boundary"}},
	{Name: "安全攻防审查", Mission: "从攻击面验证密钥、输入、权限与数据保护", Keys: []string{"security", "security review"}},
	{Name: "测试反证审查", Mission: "用失败场景和确定性测试反证实现声明", Keys: []string{"test-quality", "test quality", "测试验证", "反证测试"}},
	{Name: "数据兼容审查", Mission: "检查数据格式、迁移、边界值与向后兼容", Keys: []string{"data-compatibility", "data compatibility"}},
	{Name: "端到端体验审查", Mission: "从真实用户路径验证功能与交互闭环", Keys: []string{"e2e-ux", "end-to-end", "user experience"}},
	{Name: "稳定性成本审查", Mission: "评估可靠性、延迟、资源与费用风险", Keys: []string{"reliability-cost", "reliability cost"}},
	{Name: "对抗破坏验证", Mission: "主动构造反例寻找流程与实现的脆弱点", Keys: []string{"adversarial-falsification", "adversarial"}},
	{Name: "代码架构审查", Mission: "检查模块边界、复杂度、可维护性与集成方式", Keys: []string{"code-architecture", "code architecture", "架构审查"}},
	{Name: "主实现者", Mission: "负责从调查、实现到修复的连续交付", Keys: []string{"german-legal-billing", "main implementer", "implementation owner", "主实现者", "实现开发", "功能开发"}},
}

func newObserverPlugin() *observerPlugin {
	return &observerPlugin{}
}

func (p *observerPlugin) HandleWebhook(ctx context.Context, req *pluginsdk.WebhookRequest) (*pluginsdk.WebhookResponse, error) {
	if req == nil || req.WebhookKey != snapshotWebhookKey {
		return jsonResponse(404, map[string]string{"error": "not found"})
	}
	if !strings.EqualFold(req.Method, "GET") {
		return jsonResponse(405, map[string]string{"error": "method not allowed"})
	}
	host := p.Host()
	if host == nil {
		return jsonResponse(503, map[string]string{"error": "Kandev Host 尚未就绪"})
	}
	query, err := url.ParseQuery(req.Query)
	if err != nil {
		return jsonResponse(400, map[string]string{"error": "invalid query"})
	}
	result, err := collectSnapshot(ctx, host, strings.TrimSpace(query.Get("workspace_id")), strings.TrimSpace(query.Get("task_id")))
	if err != nil {
		return jsonResponse(502, map[string]string{"error": err.Error()})
	}
	return jsonResponse(200, result)
}

func jsonResponse(status int32, value any) (*pluginsdk.WebhookResponse, error) {
	body, err := json.Marshal(value)
	if err != nil {
		return nil, fmt.Errorf("agent observer: encode response: %w", err)
	}
	return &pluginsdk.WebhookResponse{
		Status: status,
		Headers: map[string]string{
			"Content-Type":  "application/json; charset=utf-8",
			"Cache-Control": "no-store",
		},
		Body: body,
	}, nil
}

func collectSnapshot(ctx context.Context, host pluginsdk.Host, workspaceID, taskID string) (snapshot, error) {
	tasks, err := collectTasks(ctx, host, workspaceID, taskID)
	if err != nil {
		return snapshot{}, fmt.Errorf("读取任务失败: %w", err)
	}
	taskIDs := make([]string, 0, len(tasks))
	for _, task := range tasks {
		taskIDs = append(taskIDs, task.ID)
	}
	sessions, err := collectSessions(ctx, host, workspaceID, taskIDs)
	if err != nil {
		return snapshot{}, fmt.Errorf("读取会话失败: %w", err)
	}
	messages, err := collectMessages(ctx, host, taskIDs)
	if err != nil {
		return snapshot{}, fmt.Errorf("读取消息失败: %w", err)
	}

	sessionsByTask := make(map[string][]pluginsdk.Session)
	for _, session := range sessions {
		sessionsByTask[session.TaskID] = append(sessionsByTask[session.TaskID], session)
	}
	heartbeatsByTask := latestHeartbeats(messages)
	agents := make([]agentSnapshot, 0, len(tasks))
	for _, task := range tasks {
		agents = append(agents, buildAgentSnapshot(task, sessionsByTask[task.ID], heartbeatsByTask[task.ID], time.Now().UTC()))
	}
	sort.SliceStable(agents, func(i, j int) bool {
		left, right := agentSortRank(agents[i]), agentSortRank(agents[j])
		if left != right {
			return left < right
		}
		return agents[i].LastHeartbeatAt > agents[j].LastHeartbeatAt
	})
	kandevNative, kandevEdges, kandevTimeline, err := loadKandevNativeState(ctx, host, workspaceID, taskID)
	if err != nil {
		return snapshot{}, fmt.Errorf("读取 Kandev 原生 Agent 事件失败: %w", err)
	}
	codexNative, codexEdges, codexTimeline, bridge := readCodexHookSnapshot(defaultCodexHookSnapshotPath(), workspaceID, taskID != "")
	codexHistory, historyEdges, historyTimeline, historyBridge := readCodexAppSnapshot(defaultCodexSnapshotPath(), workspaceID, taskID != "")
	edges := mergeCollaborationEdges(kandevEdges, historyEdges, codexEdges)
	timeline := append(append(kandevTimeline, historyTimeline...), codexTimeline...)
	sort.SliceStable(edges, func(i, j int) bool { return edges[i].ObservedAt < edges[j].ObservedAt })
	sort.SliceStable(timeline, func(i, j int) bool { return timeline[i].ObservedAt < timeline[j].ObservedAt })
	summary := summarizeAgents(agents)
	summary.KandevNative = len(kandevNative)
	summary.CodexNative = len(codexNative)
	summary.CodexHistory = len(codexHistory)
	summary.InferredTasks = len(agents)
	for _, edge := range edges {
		if edge.EdgeType == "correction" {
			summary.Corrections++
		}
	}
	summary.TimelineEvents = len(timeline)
	return snapshot{
		GeneratedAt:        time.Now().UTC().Format(time.RFC3339),
		WorkspaceID:        workspaceID,
		Summary:            summary,
		Agents:             agents,
		KandevNativeAgents: kandevNative,
		CodexNativeAgents:  codexNative,
		CodexHistoryAgents: codexHistory,
		Edges:              edges,
		Timeline:           timeline,
		Bridge:             bridge,
		HistoryBridge:      historyBridge,
	}, nil
}

func mergeCollaborationEdges(groups ...[]collaborationEdgeSnapshot) []collaborationEdgeSnapshot {
	result := make([]collaborationEdgeSnapshot, 0)
	indexes := make(map[string]int)
	for _, group := range groups {
		for _, edge := range group {
			key := edge.EdgeType + "|" + edge.FromAgentID + "|" + strings.Join(edge.ToAgentIDs, ",")
			if index, found := indexes[key]; found {
				result[index] = edge
				continue
			}
			indexes[key] = len(result)
			result = append(result, edge)
		}
	}
	return keepLast(result, maxNativeEdges)
}

func collectTasks(ctx context.Context, host pluginsdk.Host, workspaceID, taskID string) ([]pluginsdk.Task, error) {
	if taskID != "" {
		task, err := host.Tasks().Get(ctx, taskID)
		if err != nil {
			return nil, err
		}
		if task == nil || (workspaceID != "" && task.WorkspaceID != workspaceID) {
			return []pluginsdk.Task{}, nil
		}
		return []pluginsdk.Task{*task}, nil
	}
	filter := pluginsdk.TaskFilter{IncludeEphemeral: true}
	if workspaceID != "" {
		filter.WorkspaceIDs = []string{workspaceID}
	}
	var all []pluginsdk.Task
	cursor := ""
	for page := 0; page < maxPages && len(all) < maxTasks; page++ {
		items, info, err := host.Tasks().List(ctx, filter, pluginsdk.Page{Limit: pageLimit, Cursor: cursor})
		if err != nil {
			return nil, err
		}
		all = appendBounded(all, items, maxTasks)
		if info == nil || !info.HasMore || info.NextCursor == "" {
			break
		}
		cursor = info.NextCursor
	}
	return all, nil
}

func collectSessions(ctx context.Context, host pluginsdk.Host, workspaceID string, taskIDs []string) ([]pluginsdk.Session, error) {
	if len(taskIDs) == 0 {
		return []pluginsdk.Session{}, nil
	}
	filter := pluginsdk.SessionFilter{TaskIDs: taskIDs}
	if workspaceID != "" {
		filter.WorkspaceIDs = []string{workspaceID}
	}
	var all []pluginsdk.Session
	cursor := ""
	for page := 0; page < maxPages && len(all) < maxSessions; page++ {
		items, info, err := host.Sessions().List(ctx, filter, pluginsdk.Page{Limit: pageLimit, Cursor: cursor})
		if err != nil {
			return nil, err
		}
		all = appendBounded(all, items, maxSessions)
		if info == nil || !info.HasMore || info.NextCursor == "" {
			break
		}
		cursor = info.NextCursor
	}
	return all, nil
}

func collectMessages(ctx context.Context, host pluginsdk.Host, taskIDs []string) ([]pluginsdk.Message, error) {
	if len(taskIDs) == 0 {
		return []pluginsdk.Message{}, nil
	}
	filter := pluginsdk.MessageFilter{TaskIDs: taskIDs, Types: []string{"message"}}
	var all []pluginsdk.Message
	cursor := ""
	for page := 0; page < maxPages && len(all) < maxMessages; page++ {
		items, info, err := host.Messages().List(ctx, filter, pluginsdk.Page{Limit: pageLimit, Cursor: cursor})
		if err != nil {
			return nil, err
		}
		all = appendBounded(all, items, maxMessages)
		if info == nil || !info.HasMore || info.NextCursor == "" {
			break
		}
		cursor = info.NextCursor
	}
	return all, nil
}

func appendBounded[T any](dst, src []T, limit int) []T {
	remaining := limit - len(dst)
	if remaining <= 0 {
		return dst
	}
	if len(src) > remaining {
		src = src[:remaining]
	}
	return append(dst, src...)
}

func latestHeartbeats(messages []pluginsdk.Message) map[string]*parsedHeartbeat {
	result := make(map[string]*parsedHeartbeat)
	for _, message := range messages {
		if message.AuthorType != "agent" {
			continue
		}
		heartbeat, ok := parseHeartbeat(message.Content)
		if !ok {
			continue
		}
		heartbeat.CreatedAt = message.CreatedAt
		heartbeat.SessionID = message.SessionID
		previous := result[message.TaskID]
		if previous == nil || timestampAfter(message.CreatedAt, previous.CreatedAt) {
			copy := heartbeat
			result[message.TaskID] = &copy
		}
	}
	return result
}

func parseHeartbeat(content string) (parsedHeartbeat, bool) {
	matches := heartbeatPattern.FindStringSubmatch(content)
	if len(matches) != 8 {
		return parsedHeartbeat{}, false
	}
	return parsedHeartbeat{
		Role:       cleanField(matches[1], 32),
		State:      strings.ToLower(strings.TrimSpace(matches[2])),
		Progress:   cleanField(matches[3], 360),
		Difficulty: cleanField(matches[4], 240),
		Dependency: cleanField(matches[5], 240),
		NeedsHuman: matches[6] == "是" || strings.EqualFold(matches[6], "yes"),
		NextStep:   cleanField(matches[7], 280),
	}, true
}

func cleanField(value string, limit int) string {
	value = strings.Join(strings.Fields(strings.TrimSpace(value)), " ")
	value = secretAssignmentPattern.ReplaceAllString(value, "$1=[已脱敏]")
	value = directTokenPattern.ReplaceAllString(value, "[已脱敏]")
	value = bearerPattern.ReplaceAllString(value, "Bearer [已脱敏]")
	if utf8.RuneCountInString(value) <= limit {
		return value
	}
	runes := []rune(value)
	return string(runes[:limit-1]) + "…"
}

func timestampAfter(left, right string) bool {
	l, lerr := time.Parse(time.RFC3339Nano, left)
	r, rerr := time.Parse(time.RFC3339Nano, right)
	if lerr == nil && rerr == nil {
		return l.After(r)
	}
	return left > right
}

func buildAgentSnapshot(task pluginsdk.Task, sessions []pluginsdk.Session, heartbeat *parsedHeartbeat, now time.Time) agentSnapshot {
	latestSession := newestSession(sessions)
	role := inferRole(task, latestSession)
	state, needsHuman := inferExecutionState(task, latestSession)
	progress, difficulty, dependency, nextStep := inferredNarrative(task, state)
	heartbeatAt := task.UpdatedAt
	sourceQuality := "kandev_inference"
	sessionID := ""
	model := ""
	if latestSession != nil {
		sessionID = latestSession.ID
		model = latestSession.Model
	}
	if heartbeat != nil {
		if heartbeat.Role != "" {
			role = roleByNameOrFallback(heartbeat.Role, role)
		}
		state = heartbeatExecutionState(heartbeat.State, heartbeat.NeedsHuman)
		needsHuman = heartbeat.NeedsHuman
		progress = valueOr(heartbeat.Progress, progress)
		difficulty = valueOr(heartbeat.Difficulty, difficulty)
		dependency = valueOr(heartbeat.Dependency, dependency)
		nextStep = valueOr(heartbeat.NextStep, nextStep)
		heartbeatAt = heartbeat.CreatedAt
		sessionID = heartbeat.SessionID
		sourceQuality = "structured_agent_heartbeat"
	}
	health := "not_reported"
	if heartbeat != nil {
		health = heartbeatHealth(heartbeatAt, state, now)
	}
	return agentSnapshot{
		AgentID:           valueOr(sessionID, "task:"+task.ID),
		DisplayName:       role.Name,
		Mission:           role.Mission,
		TaskTitle:         cleanField(task.Title, 100),
		ExecutionState:    state,
		DeliveryVerdict:   inferDeliveryVerdict(task, state),
		ProgressSummary:   progress,
		CurrentDifficulty: difficulty,
		Dependency:        dependency,
		NeedsHuman:        needsHuman,
		NextStep:          nextStep,
		LastHeartbeatAt:   heartbeatAt,
		HeartbeatHealth:   health,
		SourceQuality:     sourceQuality,
		Source:            snapshotSource{TaskID: task.ID, SessionID: sessionID},
		Model:             model,
		KandevTaskState:   task.State,
		SessionCount:      len(sessions),
	}
}

func newestSession(sessions []pluginsdk.Session) *pluginsdk.Session {
	if len(sessions) == 0 {
		return nil
	}
	latest := sessions[0]
	for _, session := range sessions[1:] {
		if timestampAfter(session.StartedAt, latest.StartedAt) {
			latest = session
		}
	}
	return &latest
}

func inferRole(task pluginsdk.Task, session *pluginsdk.Session) roleDefinition {
	haystack := strings.ToLower(task.Title + " " + task.Description)
	if session != nil {
		haystack += " " + strings.ToLower(session.AgentDisplayName+" "+session.AgentProfileName)
	}
	for _, definition := range roleDefinitions {
		for _, key := range definition.Keys {
			if strings.Contains(haystack, key) {
				return definition
			}
		}
	}
	return roleDefinition{Name: "执行智能体", Mission: "推进当前任务并报告可验证的进展与阻塞"}
}

func roleByNameOrFallback(name string, fallback roleDefinition) roleDefinition {
	for _, definition := range roleDefinitions {
		if definition.Name == name {
			return definition
		}
	}
	if containsHan(name) {
		return roleDefinition{Name: cleanField(name, 12), Mission: valueOr(fallback.Mission, "推进当前职责并报告可验证结果")}
	}
	return fallback
}

func containsHan(value string) bool {
	for _, r := range value {
		if r >= '\u4e00' && r <= '\u9fff' {
			return true
		}
	}
	return false
}

func inferExecutionState(task pluginsdk.Task, session *pluginsdk.Session) (string, bool) {
	taskState := strings.ToLower(task.State)
	if task.CompletedAt != nil || containsAny(taskState, "done", "completed", "closed") {
		return "finished", false
	}
	if containsAny(taskState, "review", "approval") {
		return "waiting_on_human", true
	}
	if session != nil {
		sessionState := strings.ToLower(session.State)
		switch {
		case containsAny(sessionState, "fail", "error"):
			return "failed", false
		case containsAny(sessionState, "queue", "pending"):
			return "queued", false
		case containsAny(sessionState, "run", "work", "active", "start"):
			return "working", false
		case containsAny(sessionState, "stop", "end", "complete"):
			return "waiting_on_dependency", false
		}
	}
	if containsAny(taskState, "fail", "error", "blocked") {
		return "failed", false
	}
	if containsAny(taskState, "progress", "active", "working") {
		return "working", false
	}
	return "queued", false
}

func heartbeatExecutionState(state string, needsHuman bool) string {
	switch state {
	case "working", "starting":
		return "working"
	case "queued":
		return "queued"
	case "waiting":
		if needsHuman {
			return "waiting_on_human"
		}
		return "waiting_on_dependency"
	case "finished", "stopped":
		return "finished"
	case "failed":
		return "failed"
	default:
		return "queued"
	}
}

func inferDeliveryVerdict(task pluginsdk.Task, executionState string) string {
	state := strings.ToLower(task.State)
	switch {
	case containsAny(state, "done", "completed", "closed"):
		return "accepted"
	case containsAny(state, "fail", "rejected", "changes") || executionState == "failed":
		return "changes_required"
	case containsAny(state, "review", "approval") || executionState == "finished" || executionState == "waiting_on_human":
		return "awaiting_review"
	default:
		return "not_submitted"
	}
}

func inferredNarrative(task pluginsdk.Task, state string) (string, string, string, string) {
	title := valueOr(cleanField(task.Title, 100), "未命名任务")
	switch state {
	case "working":
		return "正在执行：" + title, "尚未报告结构化困难", "当前 Kandev 会话", "继续执行并发送结构化心跳"
	case "waiting_on_human":
		return "工作已进入人工评审阶段", "等待人的确认或裁决", "人工评审", "由人确认、退回或批准"
	case "waiting_on_dependency":
		return "当前执行已暂停，等待外部依赖", "等待依赖完成", "上游任务或评审", "依赖解除后继续"
	case "finished":
		return "执行工作已完成", "无已报告困难", "最终评审与交付裁决", "等待或核验交付结论"
	case "failed":
		return "执行失败，需要诊断", "Kandev 报告失败状态", "修复与重新验证", "检查失败证据后决定是否重试"
	default:
		return "任务已创建，尚未开始执行", "尚未报告", "执行资源与启动条件", "启动智能体"
	}
}

func heartbeatHealth(value, executionState string, now time.Time) string {
	if executionState == "finished" || executionState == "failed" {
		return "settled"
	}
	timestamp, err := time.Parse(time.RFC3339Nano, value)
	if err != nil {
		return "unknown"
	}
	age := now.Sub(timestamp)
	switch {
	case age <= 5*time.Minute:
		return "live"
	case age <= 20*time.Minute:
		return "recent"
	default:
		return "stale"
	}
}

func summarizeAgents(agents []agentSnapshot) snapshotSummary {
	summary := snapshotSummary{TotalTasks: len(agents)}
	roles := make(map[string]struct{})
	for _, agent := range agents {
		roles[agent.DisplayName] = struct{}{}
		if agent.NeedsHuman {
			summary.WaitingOnHuman++
		} else {
			switch agent.ExecutionState {
			case "working":
				summary.Working++
			case "queued":
				summary.Queued++
				summary.WaitingOnDependency++
			case "waiting_on_dependency":
				summary.WaitingOnDependency++
			case "finished":
				summary.Finished++
			case "failed":
				summary.Failed++
			}
		}
		if agent.SourceQuality == "structured_agent_heartbeat" && agent.HeartbeatHealth == "stale" {
			summary.Stale++
		}
	}
	summary.UniqueRoles = len(roles)
	return summary
}

func agentSortRank(agent agentSnapshot) int {
	if agent.NeedsHuman {
		return 0
	}
	switch agent.ExecutionState {
	case "failed":
		return 1
	case "working":
		return 2
	case "waiting_on_dependency":
		return 3
	case "queued":
		return 4
	default:
		return 5
	}
}

func containsAny(value string, candidates ...string) bool {
	for _, candidate := range candidates {
		if strings.Contains(value, candidate) {
			return true
		}
	}
	return false
}

func valueOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}
