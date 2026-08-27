package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/kandev/kandev/pkg/pluginsdk"
)

func TestProjectKandevNativeEventKeepsOnlyStructuredFacts(t *testing.T) {
	event := &pluginsdk.Event{
		EventID: "evt-1", EventType: "agent.stream.session-1", OccurredAt: "2026-08-26T10:00:00Z", WorkspaceID: "workspace-1",
		Payload: map[string]any{
			"task_id": "task-1", "session_id": "session-1", "timestamp": "2026-08-26T10:00:00Z",
			"data": map[string]any{
				"acp_session_id": "parent-1", "tool_status": "running", "tool_call_id": "tool-1",
				"normalized": map[string]any{
					"kind": "subagent_task",
					"subagent_task": map[string]any{
						"description": "负责技术调研；token=do-not-store", "prompt": "邮件正文和 API Key 都不能落盘",
						"child_session_id": "child-1", "status": "running",
					},
				},
			},
		},
	}
	projection, ok := projectKandevNativeEvent(event)
	if !ok {
		t.Fatal("expected a normalized subagent event")
	}
	if projection.Agent.DisplayName != "技术调研员" || projection.Agent.ExecutionState != "working" {
		t.Fatalf("unexpected agent projection: %#v", projection.Agent)
	}
	if projection.Edge == nil || projection.Edge.EdgeType != "spawn" {
		t.Fatalf("expected an exact spawn edge: %#v", projection.Edge)
	}
	encoded, _ := json.Marshal(projection)
	for _, forbidden := range []string{"do-not-store", "邮件正文", "API Key", "prompt", "description"} {
		if stringContains(string(encoded), forbidden) {
			t.Fatalf("projection leaked forbidden input %q: %s", forbidden, encoded)
		}
	}
}

func TestReadCodexHistoryNeverClaimsRealtimeAndSynthesizesParentEdge(t *testing.T) {
	temporary := t.TempDir()
	path := filepath.Join(temporary, "snapshot.json")
	now := time.Now().UTC().Format(time.RFC3339Nano)
	value := map[string]any{
		"protocol_version": "codex-app-server-history/v1", "generated_at": now, "kandev_workspace_id": "workspace-1",
		"bridge": map[string]any{"state": "history_synced", "source": "codex_app_server_history", "root_thread_id": "root-1", "last_success_at": now},
		"agents": []any{map[string]any{
			"agent_id": "child-1", "parent_agent_id": "root-1", "display_name": "测试验证员", "role_cn": "测试验证员",
			"execution_state": "working", "progress_summary": "运行中 token=must-hide", "current_difficulty": "无", "created_at": now, "last_activity_at": now,
		}},
		"edges": []any{}, "timeline": []any{},
	}
	encoded, _ := json.Marshal(value)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	agents, edges, _, health := readCodexAppSnapshot(path, "workspace-1", false)
	if health.State != "history_synced" || len(agents) != 1 || len(edges) != 1 {
		t.Fatalf("unexpected snapshot projection: health=%#v agents=%#v edges=%#v", health, agents, edges)
	}
	if agents[0].ExecutionState != "historical" || stringContains(agents[0].ProgressSummary, "must-hide") || edges[0].EdgeType != "spawn" {
		t.Fatalf("expected history-only semantics and exact parent edge: %#v %#v", agents[0], edges[0])
	}
}

func TestReadCodexHookSnapshotKeepsRealtimeLifecycleAndContractFields(t *testing.T) {
	temporary := t.TempDir()
	path := filepath.Join(temporary, "hook.json")
	now := time.Now().UTC().Format(time.RFC3339Nano)
	value := map[string]any{
		"protocol_version": "codex-hooks-observer/v2", "generated_at": now, "kandev_workspace_id": "workspace-1",
		"bridge": map[string]any{"state": "active", "source": "codex_hooks", "root_thread_id": "root-1", "last_success_at": now, "active_runs": 1, "run_count": 2},
		"runs":   []any{map[string]any{"root_thread_id": "root-1", "execution_state": "active", "updated_at": now}},
		"agents": []any{map[string]any{
			"agent_id": "child-1", "parent_agent_id": "root-1", "display_name": "测试反证审查", "role_cn": "测试反证审查",
			"root_thread_id":  "root-1",
			"execution_state": "stopped", "progress_summary": "邮件正文 must-not-pass", "current_difficulty": "api_key='value with spaces'", "created_at": now, "last_activity_at": now,
		}},
		"edges": []any{map[string]any{
			"edge_id": "correction-1", "edge_type": "correction", "from_agent_id": "root-1", "to_agent_ids": []string{"child-1"},
			"root_thread_id": "root-1", "status": "completed", "observed_at": now, "summary": "untrusted summary",
		}},
		"timeline": []any{
			map[string]any{
				"event_id": "wait-1", "event_type": "wait", "actor_agent_id": "root-1", "target_agent_ids": []string{"child-1"},
				"status": "completed", "observed_at": now, "summary": "untrusted summary", "repeat_count": 2,
			},
			map[string]any{
				"event_id": "stop-1", "event_type": "stopped", "actor_agent_id": "root-1", "target_agent_ids": []string{"child-1"},
				"status": "stopped", "observed_at": now, "summary": "untrusted stop summary",
			},
		},
	}
	encoded, _ := json.Marshal(value)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "ready", "")
	agents, edges, timeline, health := readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "active" || health.ActiveRuns != 1 || health.RunCount != 2 || len(agents) != 1 || len(edges) != 1 || len(timeline) != 2 {
		t.Fatalf("unexpected hook projection: health=%#v agents=%#v edges=%#v timeline=%#v", health, agents, edges, timeline)
	}
	if agents[0].ExecutionState != "stopped" || agents[0].RootThreadID != "root-1" || edges[0].RootThreadID != "root-1" || stringContains(agents[0].ProgressSummary, "must-not-pass") {
		t.Fatalf("hook state or privacy projection failed: %#v", agents[0])
	}
	if edges[0].EdgeType != "correction" || timeline[0].RepeatCount != 2 || timeline[1].EventType != "stopped" || timeline[1].Status != "stopped" {
		t.Fatalf("correction/repeat_count contract was lost: %#v %#v", edges[0], timeline[0])
	}
	projected, _ := json.Marshal([]any{agents, edges, timeline})
	for _, forbidden := range []string{"邮件正文", "value with spaces", "untrusted summary", "untrusted stop summary"} {
		if stringContains(string(projected), forbidden) {
			t.Fatalf("hook projection leaked untrusted text %q: %s", forbidden, projected)
		}
	}
}

func TestReadCodexHookSnapshotProjectsWaitingActivityAndSessionEvents(t *testing.T) {
	temporary := t.TempDir()
	path := filepath.Join(temporary, "hook.json")
	now := time.Now().UTC().Format(time.RFC3339Nano)
	value := map[string]any{
		"protocol_version": "codex-hooks-observer/v2", "generated_at": now, "kandev_workspace_id": "workspace-1",
		"bridge": map[string]any{"state": "active", "source": "codex_hooks", "root_thread_id": "root-2", "last_success_at": now, "active_runs": 2, "run_count": 2},
		"runs": []any{
			map[string]any{"root_thread_id": "root-1", "execution_state": "active", "updated_at": now},
			map[string]any{"root_thread_id": "root-2", "execution_state": "active", "updated_at": now},
		},
		"agents": []any{
			map[string]any{
				"agent_id": "child-1", "parent_agent_id": "root-1", "root_thread_id": "root-1", "role_cn": "测试验证员",
				"execution_state": "waiting_on_human", "progress_summary": "正在等待权限审批",
				"current_difficulty": "继续执行需要人类决定是否授予本次权限", "created_at": now, "last_activity_at": now,
			},
			map[string]any{
				"agent_id": "child-2", "parent_agent_id": "root-2", "root_thread_id": "root-2", "role_cn": "测试验证员",
				"execution_state": "working", "progress_summary": "已完成一项文件修改",
				"current_difficulty": "DEEPSEEK_API_KEY=must-hide", "created_at": now, "last_activity_at": now,
			},
		},
		"timeline": []any{
			map[string]any{"event_id": "permission-1", "event_type": "permission", "actor_agent_id": "child-1", "root_thread_id": "root-1", "status": "waiting", "observed_at": now, "summary": "secret"},
			map[string]any{"event_id": "activity-1", "event_type": "activity", "actor_agent_id": "child-2", "root_thread_id": "root-2", "activity_kind": "file_change", "status": "failed", "observed_at": now, "summary": "mail body"},
			map[string]any{"event_id": "session-start-1", "event_type": "session_start", "actor_agent_id": "root-1", "root_thread_id": "root-1", "status": "started", "observed_at": now, "summary": "untrusted"},
			map[string]any{"event_id": "session-end-1", "event_type": "session_end", "actor_agent_id": "root-1", "root_thread_id": "root-1", "status": "completed", "observed_at": now, "summary": "untrusted"},
		},
	}
	encoded, _ := json.Marshal(value)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "ready", "")
	agents, _, timeline, health := readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "active" || health.ActiveRuns != 2 || health.RunCount != 2 {
		t.Fatalf("unexpected bridge health: %#v", health)
	}
	if len(agents) != 2 || agents[0].DisplayName != "测试验证员" || agents[1].DisplayName != "测试验证员" {
		t.Fatalf("names should be scoped per root task: %#v", agents)
	}
	if agents[0].ExecutionState != "waiting_on_human" || agents[0].ProgressSummary != "正在等待权限审批" {
		t.Fatalf("waiting projection was lost: %#v", agents[0])
	}
	if len(timeline) != 4 || timeline[1].ActivityKind != "file_change" || timeline[1].Summary != "子 Agent 的工程文件修改失败" {
		t.Fatalf("activity/session timeline projection failed: %#v", timeline)
	}
	projected, _ := json.Marshal([]any{agents, timeline})
	for _, forbidden := range []string{"must-hide", "mail body", "secret", "untrusted"} {
		if stringContains(string(projected), forbidden) {
			t.Fatalf("projection leaked untrusted text %q: %s", forbidden, projected)
		}
	}
}

func TestMergeCollaborationEdgesKeepsSameShapeFromDifferentRootTasks(t *testing.T) {
	edges := mergeCollaborationEdges(
		[]collaborationEdgeSnapshot{{EdgeType: "spawn", FromAgentID: "parent", ToAgentIDs: []string{"child"}, RootThreadID: "root-1"}},
		[]collaborationEdgeSnapshot{{EdgeType: "spawn", FromAgentID: "parent", ToAgentIDs: []string{"child"}, RootThreadID: "root-2"}},
	)
	if len(edges) != 2 {
		t.Fatalf("cross-task edges were incorrectly merged: %#v", edges)
	}
}

func TestReadCodexSnapshotRejectsOtherWorkspace(t *testing.T) {
	path := filepath.Join(t.TempDir(), "snapshot.json")
	encoded, _ := json.Marshal(map[string]any{
		"protocol_version": "codex-app-server-history/v1", "kandev_workspace_id": "workspace-a", "bridge": map[string]any{"state": "history_synced"},
	})
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	agents, _, _, health := readCodexAppSnapshot(path, "workspace-b", false)
	if health.State != "out_of_scope" || len(agents) != 0 {
		t.Fatalf("workspace isolation failed: %#v %#v", health, agents)
	}
}

func TestReadCodexHookSnapshotFailsClosedWhenStaleOrDeliveryFailed(t *testing.T) {
	temporary := t.TempDir()
	path := filepath.Join(temporary, "hook.json")
	stale := time.Now().UTC().Add(-20 * time.Minute).Format(time.RFC3339Nano)
	value := map[string]any{
		"protocol_version": "codex-hooks-observer/v2", "generated_at": stale, "kandev_workspace_id": "workspace-1",
		"bridge": map[string]any{"state": "active", "root_thread_id": "root-1", "last_success_at": stale, "active_runs": 1},
		"runs":   []any{map[string]any{"root_thread_id": "root-1", "execution_state": "active", "updated_at": stale}},
		"agents": []any{map[string]any{"agent_id": "child-1", "parent_agent_id": "root-1", "root_thread_id": "root-1", "execution_state": "working"}},
	}
	encoded, _ := json.Marshal(value)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	writeCodexDeliveryHealth(t, temporary, time.Now().UTC().Format(time.RFC3339Nano), "workspace-1", "ready", "")
	agents, _, _, health := readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "stale" || health.ActiveRuns != 0 || len(agents) != 0 {
		t.Fatalf("stale snapshot must not expose realtime agents: %#v %#v", health, agents)
	}

	now := time.Now().UTC().Format(time.RFC3339Nano)
	value["generated_at"] = now
	value["bridge"] = map[string]any{"state": "active", "root_thread_id": "root-1", "last_success_at": now, "active_runs": 1}
	value["runs"] = []any{map[string]any{"root_thread_id": "root-1", "execution_state": "active", "updated_at": now}}
	encoded, _ = json.Marshal(value)
	if err := os.WriteFile(path, encoded, 0o600); err != nil {
		t.Fatal(err)
	}
	writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "delivery_failed", "receiver_failed")
	agents, _, _, health = readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "delivery_failed" || len(agents) != 0 {
		t.Fatalf("delivery failure must override a fresh snapshot: %#v %#v", health, agents)
	}
}

func TestReadCodexHookSnapshotShowsDeliveryFailureWithoutUsableSnapshot(t *testing.T) {
	temporary := t.TempDir()
	path := filepath.Join(temporary, "hook.json")
	now := time.Now().UTC().Format(time.RFC3339Nano)
	writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "delivery_failed", "receiver_failed")

	agents, _, _, health := readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "delivery_failed" || len(agents) != 0 {
		t.Fatalf("missing snapshot must preserve delivery failure: %#v %#v", health, agents)
	}
	if err := os.WriteFile(path, []byte("not-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	agents, _, _, health = readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "delivery_failed" || len(agents) != 0 {
		t.Fatalf("invalid snapshot must preserve delivery failure: %#v %#v", health, agents)
	}
}

func TestReadCodexHookSnapshotNeverShowsLiveAgentFromNonActiveBridge(t *testing.T) {
	for _, bridgeState := range []string{"idle", "ready"} {
		t.Run(bridgeState, func(t *testing.T) {
			temporary := t.TempDir()
			path := filepath.Join(temporary, "hook.json")
			now := time.Now().UTC().Format(time.RFC3339Nano)
			value := map[string]any{
				"protocol_version": "codex-hooks-observer/v2", "generated_at": now, "kandev_workspace_id": "workspace-1",
				"bridge": map[string]any{"state": bridgeState, "root_thread_id": "root-1", "last_success_at": now},
				"runs":   []any{map[string]any{"root_thread_id": "root-1", "execution_state": "active", "updated_at": now}},
				"agents": []any{
					map[string]any{"agent_id": "child-working", "parent_agent_id": "root-1", "root_thread_id": "root-1", "execution_state": "working"},
					map[string]any{"agent_id": "child-waiting", "parent_agent_id": "root-1", "root_thread_id": "root-1", "execution_state": "waiting_on_human"},
				},
			}
			encoded, _ := json.Marshal(value)
			if err := os.WriteFile(path, encoded, 0o600); err != nil {
				t.Fatal(err)
			}
			writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "ready", "")
			agents, _, _, _ := readCodexHookSnapshot(path, "workspace-1", false)
			if len(agents) != 0 {
				t.Fatalf("non-active bridge exposed live agents: %#v", agents)
			}
		})
	}
}

func TestReadCodexHookSnapshotReportsDeliveryFailureBeforeMissingSnapshot(t *testing.T) {
	temporary := t.TempDir()
	missing := filepath.Join(temporary, "missing-hook.json")
	now := time.Now().UTC().Format(time.RFC3339Nano)
	writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "delivery_failed", "receiver_failed")
	agents, edges, timeline, health := readCodexHookSnapshot(missing, "workspace-1", false)
	if health.State != "delivery_failed" || health.Source != "codex_hook_dispatch" {
		t.Fatalf("verified delivery failure must outrank a missing snapshot: %#v", health)
	}
	if len(agents) != 0 || len(edges) != 0 || len(timeline) != 0 {
		t.Fatalf("delivery failure must not expose lifecycle data: %#v %#v %#v", agents, edges, timeline)
	}
}

func TestReadCodexHookSnapshotNeverProjectsWorkingAgentWithoutFreshActiveRun(t *testing.T) {
	for _, bridgeState := range []string{"idle", "ready"} {
		t.Run(bridgeState, func(t *testing.T) {
			temporary := t.TempDir()
			path := filepath.Join(temporary, "hook.json")
			now := time.Now().UTC().Format(time.RFC3339Nano)
			value := map[string]any{
				"protocol_version": "codex-hooks-observer/v2", "generated_at": now, "kandev_workspace_id": "workspace-1",
				"bridge": map[string]any{"state": bridgeState, "root_thread_id": "root-1", "last_success_at": now},
				"runs":   []any{map[string]any{"root_thread_id": "root-1", "execution_state": "idle", "updated_at": now}},
				"agents": []any{
					map[string]any{"agent_id": "working-child", "parent_agent_id": "root-1", "root_thread_id": "root-1", "execution_state": "working"},
					map[string]any{"agent_id": "settled-child", "parent_agent_id": "root-1", "root_thread_id": "root-1", "execution_state": "stopped"},
				},
			}
			encoded, _ := json.Marshal(value)
			if err := os.WriteFile(path, encoded, 0o600); err != nil {
				t.Fatal(err)
			}
			writeCodexDeliveryHealth(t, temporary, now, "workspace-1", "ready", "")
			agents, _, _, _ := readCodexHookSnapshot(path, "workspace-1", false)
			if len(agents) != 1 || agents[0].AgentID != "settled-child" {
				t.Fatalf("working agent without a fresh active run leaked for bridge %q: %#v", bridgeState, agents)
			}
		})
	}
}

func writeCodexDeliveryHealth(t *testing.T, directory, observedAt, workspaceID, state, reason string) {
	t.Helper()
	encoded, _ := json.Marshal(map[string]any{
		"protocol_version":    "codex-hook-bridge-health/v1",
		"observed_at":         observedAt,
		"state":               state,
		"reason_code":         reason,
		"kandev_workspace_id": workspaceID,
	})
	if err := os.WriteFile(filepath.Join(directory, "codex-hook-bridge-health.json"), encoded, 0o600); err != nil {
		t.Fatal(err)
	}
}

func stringContains(value, fragment string) bool {
	return len(fragment) > 0 && len(value) >= len(fragment) && (value == fragment || containsAny(value, fragment))
}
