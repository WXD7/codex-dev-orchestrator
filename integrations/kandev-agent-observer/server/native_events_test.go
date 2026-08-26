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
		"protocol_version": "codex-hooks-observer/v1", "generated_at": now, "kandev_workspace_id": "workspace-1",
		"bridge": map[string]any{"state": "ready", "source": "codex_hooks", "root_thread_id": "root-1", "last_success_at": now},
		"agents": []any{map[string]any{
			"agent_id": "child-1", "parent_agent_id": "root-1", "display_name": "测试反证审查", "role_cn": "测试反证审查",
			"execution_state": "stopped", "progress_summary": "邮件正文 must-not-pass", "current_difficulty": "api_key='value with spaces'", "created_at": now, "last_activity_at": now,
		}},
		"edges": []any{map[string]any{
			"edge_id": "correction-1", "edge_type": "correction", "from_agent_id": "root-1", "to_agent_ids": []string{"child-1"},
			"status": "completed", "observed_at": now, "summary": "untrusted summary",
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
	agents, edges, timeline, health := readCodexHookSnapshot(path, "workspace-1", false)
	if health.State != "ready" || len(agents) != 1 || len(edges) != 1 || len(timeline) != 2 {
		t.Fatalf("unexpected hook projection: health=%#v agents=%#v edges=%#v timeline=%#v", health, agents, edges, timeline)
	}
	if agents[0].ExecutionState != "stopped" || stringContains(agents[0].ProgressSummary, "must-not-pass") {
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

func stringContains(value, fragment string) bool {
	return len(fragment) > 0 && len(value) >= len(fragment) && (value == fragment || containsAny(value, fragment))
}
