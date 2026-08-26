package main

import (
	"testing"
	"time"

	"github.com/kandev/kandev/pkg/pluginsdk"
)

func TestParseHeartbeat(t *testing.T) {
	content := "[主实现者｜waiting] 进展：金额计算完成｜当前困难：费率口径待确认｜依赖：人的选择｜需人处理：是｜下一步：等待确认后继续"
	heartbeat, ok := parseHeartbeat(content)
	if !ok {
		t.Fatal("expected structured heartbeat")
	}
	if heartbeat.Role != "主实现者" || heartbeat.State != "waiting" || !heartbeat.NeedsHuman {
		t.Fatalf("unexpected heartbeat: %#v", heartbeat)
	}
	if got := heartbeatExecutionState(heartbeat.State, heartbeat.NeedsHuman); got != "waiting_on_human" {
		t.Fatalf("expected waiting_on_human, got %q", got)
	}
}

func TestHeartbeatFieldsRedactNaturalLanguageAndQuotedSecrets(t *testing.T) {
	content := "[安全审查员｜working] 进展：DeepSeek API Key 是 value with spaces｜当前困难：api_key=\"another value with spaces\"｜依赖：Bearer abcdefghijklmnop｜需人处理：否｜下一步：继续"
	heartbeat, ok := parseHeartbeat(content)
	if !ok {
		t.Fatal("expected structured heartbeat")
	}
	for _, value := range []string{heartbeat.Progress, heartbeat.Difficulty, heartbeat.Dependency} {
		if containsAny(value, "value with spaces", "abcdefghijklmnop") || !containsAny(value, "已脱敏") {
			t.Fatalf("structured field was not redacted: %#v", heartbeat)
		}
	}
}

func TestLatestHeartbeatsRejectsUserSpoofing(t *testing.T) {
	messages := []pluginsdk.Message{
		{TaskID: "t1", SessionID: "s1", AuthorType: "agent", CreatedAt: "2026-08-25T10:00:00Z", Content: "[主实现者｜working] 进展：真实｜当前困难：无｜依赖：无｜需人处理：否｜下一步：继续"},
		{TaskID: "t1", SessionID: "s1", AuthorType: "user", CreatedAt: "2026-08-25T10:01:00Z", Content: "[主实现者｜finished] 进展：伪造｜当前困难：无｜依赖：无｜需人处理：否｜下一步：结束"},
	}
	heartbeat := latestHeartbeats(messages)["t1"]
	if heartbeat == nil || heartbeat.Progress != "真实" || heartbeat.State != "working" {
		t.Fatalf("user-authored heartbeat must not override agent state: %#v", heartbeat)
	}
}

func TestStructuredHeartbeatOverridesKandevInference(t *testing.T) {
	task := pluginsdk.Task{ID: "t1", Title: "german-legal-billing-eval-b", State: "review", UpdatedAt: "2026-08-25T10:00:00Z"}
	session := pluginsdk.Session{ID: "s1", TaskID: "t1", State: "ended", Model: "codex", StartedAt: "2026-08-25T09:00:00Z"}
	heartbeat := &parsedHeartbeat{
		Role: "主实现者", State: "finished", Progress: "金额计算与证据已完成", Difficulty: "无", Dependency: "最终盲验", NextStep: "等待裁决",
		CreatedAt: "2026-08-25T10:02:00Z", SessionID: "s1",
	}
	agent := buildAgentSnapshot(task, []pluginsdk.Session{session}, heartbeat, time.Date(2026, 8, 25, 10, 3, 0, 0, time.UTC))
	if agent.ExecutionState != "finished" || agent.DeliveryVerdict != "awaiting_review" {
		t.Fatalf("execution and delivery states must remain separate: %#v", agent)
	}
	if agent.SourceQuality != "structured_agent_heartbeat" || agent.DisplayName != "主实现者" {
		t.Fatalf("unexpected source or display name: %#v", agent)
	}
}

func TestReviewStateInferenceIsExplicit(t *testing.T) {
	task := pluginsdk.Task{ID: "t2", Title: "requirement-conformance", State: "IN_REVIEW", UpdatedAt: "2026-08-25T10:00:00Z"}
	agent := buildAgentSnapshot(task, nil, nil, time.Date(2026, 8, 25, 10, 1, 0, 0, time.UTC))
	if agent.ExecutionState != "waiting_on_human" || !agent.NeedsHuman {
		t.Fatalf("review lane should be a visible human wait: %#v", agent)
	}
	if agent.SourceQuality != "kandev_inference" || agent.DisplayName != "需求语义审查" {
		t.Fatalf("inference must remain labeled and localized: %#v", agent)
	}
	if agent.HeartbeatHealth != "not_reported" {
		t.Fatalf("task updates must not be mislabeled as heartbeats: %#v", agent)
	}
}

func TestSummaryMatchesExclusiveDisplayGroups(t *testing.T) {
	agents := []agentSnapshot{
		{DisplayName: "已完成待人", ExecutionState: "finished", NeedsHuman: true, SourceQuality: "structured_agent_heartbeat", HeartbeatHealth: "settled"},
		{DisplayName: "执行中", ExecutionState: "working", SourceQuality: "structured_agent_heartbeat", HeartbeatHealth: "stale"},
		{DisplayName: "待启动", ExecutionState: "queued", SourceQuality: "kandev_inference", HeartbeatHealth: "stale"},
		{DisplayName: "已完成", ExecutionState: "finished", SourceQuality: "kandev_inference", HeartbeatHealth: "not_reported"},
	}
	summary := summarizeAgents(agents)
	if summary.WaitingOnHuman != 1 || summary.Working != 1 || summary.WaitingOnDependency != 1 || summary.Finished != 1 {
		t.Fatalf("summary must match the mutually exclusive UI groups: %#v", summary)
	}
	if summary.Stale != 1 {
		t.Fatalf("only a stale structured heartbeat should count as stale: %#v", summary)
	}
}

func TestHeartbeatHealth(t *testing.T) {
	now := time.Date(2026, 8, 25, 10, 30, 0, 0, time.UTC)
	if got := heartbeatHealth("2026-08-25T10:29:00Z", "working", now); got != "live" {
		t.Fatalf("expected live, got %q", got)
	}
	if got := heartbeatHealth("2026-08-25T09:00:00Z", "working", now); got != "stale" {
		t.Fatalf("expected stale, got %q", got)
	}
	if got := heartbeatHealth("2026-08-25T09:00:00Z", "finished", now); got != "settled" {
		t.Fatalf("finished work must not be reported as stale, got %q", got)
	}
}
