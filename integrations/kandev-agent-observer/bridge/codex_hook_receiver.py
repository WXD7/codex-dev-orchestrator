#!/usr/bin/env python3
"""Persist a privacy-minimal Codex Hook lifecycle snapshot for Kandev.

Codex sends one hook event as JSON on stdin.  This receiver deliberately keeps
only identifiers, event enums, derived Chinese role labels, states, and times.
It never serializes transcripts, prompts, assistant messages, tool input, or
tool responses.  Concurrent hook processes merge through an advisory file lock
and atomically replace one mode-0600 snapshot.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid


PROTOCOL_VERSION = "codex-hooks-observer/v2"
MAX_INPUT_BYTES = 1 << 20
MAX_AGENTS = 128
MAX_EDGES = 256
MAX_TIMELINE = 512
MAX_PENDING_SPAWNS = 64
MAX_RUNS = 32

SAFE_ID = re.compile(r"^[A-Za-z0-9/][A-Za-z0-9._:/-]{0,127}$")
SAFE_ROLE_HINT = re.compile(r"^[A-Za-z0-9_./-]{1,64}$")

ROLE_LABELS: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (("intent", "requirement", "clarif"), "意图检查员"),
    (("research", "explor", "scout"), "技术调研员"),
    (("security", "safety", "privacy"), "安全审查员"),
    (("review", "critic", "audit", "judge"), "独立审查员"),
    (("test", "qa", "verif"), "测试验证员"),
    (("architect", "design"), "架构设计员"),
    (("coordinat", "orchestrat", "manager", "supervis"), "协同调度员"),
    (("implement", "worker", "developer", "coder", "default"), "实现智能体"),
)

TOOL_ACTIONS = {
    "send_message": ("correction", "上级向子 Agent 发送补充要求"),
    "send_input": ("correction", "上级向子 Agent 发送纠偏"),
    "followup_task": ("correction", "上级向子 Agent 发送纠偏并继续执行"),
    "resume_agent": ("resume", "上级恢复子 Agent"),
    "wait_agent": ("wait", "上级等待子 Agent 结果"),
    "wait": ("wait", "上级等待子 Agent 结果"),
    "interrupt_agent": ("close", "上级中断子 Agent"),
    "close_agent": ("close", "上级关闭子 Agent"),
}

TOOL_ACTIVITY = {
    "bash": ("command", "正在运行本地命令或检查", "已完成本地命令或检查"),
    "exec_command": ("command", "正在运行本地命令或检查", "已完成本地命令或检查"),
    "write_stdin": ("command", "正在等待或继续本地命令", "本地命令已返回新结果"),
    "apply_patch": ("file_change", "正在修改工程文件", "已完成一项文件修改"),
    "edit": ("file_change", "正在修改工程文件", "已完成一项文件修改"),
    "write": ("file_change", "正在写入工程文件", "已完成一项文件写入"),
    "update_plan": ("plan", "正在更新执行计划", "已完成执行计划更新"),
    "view_image": ("evidence", "正在检查可视化证据", "已完成可视化证据检查"),
    "request_user_input": ("human_input", "正在请求人类确认", "人类确认请求已处理"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if SAFE_ID.fullmatch(value) else ""


def safe_time(value: Any, fallback: Optional[str] = None) -> str:
    if isinstance(value, str) and len(value) <= 40:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return fallback or utc_now()


def optional_time(value: Any) -> str:
    if isinstance(value, str) and len(value) <= 40:
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
            return value
        except ValueError:
            pass
    return ""


def role_cn(value: Any) -> str:
    if not isinstance(value, str) or not SAFE_ROLE_HINT.fullmatch(value.strip()):
        return "执行智能体"
    normalized = value.strip().lower()
    for keys, label in ROLE_LABELS:
        if any(key in normalized for key in keys):
            return label
    return "执行智能体"


def default_output_path() -> Path:
    override = os.environ.get("KANDEV_AGENT_OBSERVER_HOOK_EVENTS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kandev" / "plugins" / "ai-delivery-agent-observer" / "data" / "codex-hook-snapshot.json"


def _empty_snapshot(workspace_id: str) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": "",
        "kandev_workspace_id": safe_id(workspace_id),
        "bridge": {
            "state": "ready",
            "source": "codex_hooks",
            "root_thread_id": "",
            "last_success_at": "",
            "error": "",
        },
        "agents": [],
        "edges": [],
        "timeline": [],
        "pending_spawns": [],
        "runs": [],
    }


def _load_snapshot(path: Path, workspace_id: str) -> Dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 << 20:
            return _empty_snapshot(workspace_id)
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty_snapshot(workspace_id)
    if not isinstance(loaded, dict) or loaded.get("protocol_version") != PROTOCOL_VERSION:
        return _empty_snapshot(workspace_id)
    existing_scope = safe_id(loaded.get("kandev_workspace_id"))
    requested_scope = safe_id(workspace_id)
    if existing_scope and requested_scope and existing_scope != requested_scope:
        return _empty_snapshot(requested_scope)
    result = _empty_snapshot(requested_scope or existing_scope)
    for key, limit in (
        ("agents", MAX_AGENTS),
        ("edges", MAX_EDGES),
        ("timeline", MAX_TIMELINE),
        ("pending_spawns", MAX_PENDING_SPAWNS),
        ("runs", MAX_RUNS),
    ):
        values = loaded.get(key)
        if isinstance(values, list):
            result[key] = [item for item in values[-limit:] if isinstance(item, dict)]
    active_runs, run_count = _run_counts(result)
    loaded_bridge = loaded.get("bridge")
    last_success_at = ""
    root_thread_id = ""
    if isinstance(loaded_bridge, dict):
        last_success_at = optional_time(loaded_bridge.get("last_success_at"))
        root_thread_id = safe_id(loaded_bridge.get("root_thread_id"))
    result["generated_at"] = optional_time(loaded.get("generated_at"))
    result["bridge"] = {
        "state": "active" if active_runs else ("idle" if run_count else "ready"),
        "source": "codex_hooks",
        "root_thread_id": root_thread_id,
        "last_success_at": last_success_at,
        "error": "",
        "active_runs": active_runs,
        "run_count": run_count,
    }
    return result


def _atomic_write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    temporary: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".codex-hook-observer-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(path))
        temporary = None
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _upsert(values: List[Dict[str, Any]], key: str, incoming: Dict[str, Any], limit: int) -> None:
    identity = incoming.get(key)
    for index, value in enumerate(values):
        if value.get(key) == identity:
            values[index] = incoming
            return
    values.append(incoming)
    del values[:-limit]


def _record_run(snapshot: Dict[str, Any], root_thread_id: str, state: str, now: str) -> None:
    if not root_thread_id:
        return
    existing = next(
        (item for item in snapshot["runs"] if item.get("root_thread_id") == root_thread_id),
        None,
    )
    run = {
        "root_thread_id": root_thread_id,
        "execution_state": state,
        "started_at": (existing or {}).get("started_at") or now,
        "updated_at": now,
        "ended_at": now if state == "idle" else "",
    }
    _upsert(snapshot["runs"], "root_thread_id", run, MAX_RUNS)


def _run_counts(snapshot: Dict[str, Any]) -> Tuple[int, int]:
    runs = snapshot.get("runs", [])
    active = sum(1 for item in runs if item.get("execution_state") == "active")
    return active, len(runs)


def _spawn_context_for_start(
    snapshot: Dict[str, Any], session_id: str, agent_type: Any, agent_id: str
) -> Tuple[str, str]:
    direct = role_cn(agent_type)
    pending = snapshot.get("pending_spawns", [])
    exact = next(
        (
            item
            for item in reversed(pending)
            if item.get("session_id") == session_id and item.get("matched_agent_id") == agent_id
        ),
        None,
    )
    if isinstance(exact, dict):
        return exact.get("role_cn") or direct, exact.get("parent_agent_id") or session_id

    # A start event does not carry the spawning tool-use ID. Even when only
    # one request is currently unmatched, arrival order is not proof of
    # identity: another parent or delayed PostToolUse can interleave here.
    # Keep the start's own role/root until PostToolUse proves the exact child.
    return direct, session_id


def _fixed_narrative(state: str) -> Tuple[str, str]:
    if state == "working":
        return "Codex Hook 已确认该 Agent 正在运行", "Hook 未提供结构化困难字段"
    if state == "stopped":
        return "Codex Hook 已确认该 Agent 已停止", "无可验证的困难字段"
    return "Codex Hook 已观察到该 Agent", "Hook 未提供结构化困难字段"


def _record_subagent(snapshot: Dict[str, Any], payload: Dict[str, Any], started: bool, now: str) -> bool:
    session_id = safe_id(payload.get("session_id"))
    agent_id = safe_id(payload.get("agent_id"))
    if not session_id or not agent_id:
        return False
    # SubagentStop proves that the lifecycle stopped, not that the assigned
    # work succeeded. Keep that distinction all the way through the UI.
    state = "working" if started else "stopped"
    progress, difficulty = _fixed_narrative(state)
    existing = next((item for item in snapshot["agents"] if item.get("agent_id") == agent_id), None)
    created_at = (existing or {}).get("created_at") or now
    inferred_role, inferred_parent = _spawn_context_for_start(
        snapshot, session_id, payload.get("agent_type"), agent_id
    )
    role = (existing or {}).get("role_cn") or inferred_role
    parent_id = (existing or {}).get("parent_agent_id") or inferred_parent
    agent = {
        "agent_id": agent_id,
        "parent_agent_id": parent_id,
        "root_thread_id": session_id,
        "display_name": role,
        "role_cn": role,
        "execution_state": state,
        "progress_summary": progress,
        "current_difficulty": difficulty,
        "created_at": created_at,
        "updated_at": now,
        "last_activity_at": now,
        "source_quality": "codex_hooks_realtime",
        "source": "codex_hooks",
    }
    _upsert(snapshot["agents"], "agent_id", agent, MAX_AGENTS)
    if started:
        edge = {
            "edge_id": "hook:spawn:" + agent_id,
            "edge_type": "spawn",
            "action": "SubagentStart",
            "from_agent_id": parent_id,
            "to_agent_ids": [agent_id],
            "root_thread_id": session_id,
            "status": "started",
            "observed_at": now,
            "summary": "Codex Hook 确认子 Agent 已开始",
            "source_quality": "codex_hooks_realtime",
            "source": "codex_hooks",
        }
        _upsert(snapshot["edges"], "edge_id", edge, MAX_EDGES)
    event_type = "spawn" if started else "stopped"
    event = {
        "event_id": "hook:" + event_type + ":" + agent_id,
        "event_type": event_type,
        "actor_agent_id": parent_id,
        "target_agent_ids": [agent_id],
        "root_thread_id": session_id,
        "status": "started" if started else "stopped",
        "observed_at": now,
        "summary": "Codex Hook 确认子 Agent 已开始" if started else "Codex Hook 确认子 Agent 已停止",
        "source_quality": "codex_hooks_realtime",
        "source": "codex_hooks",
    }
    _upsert(snapshot["timeline"], "event_id", event, MAX_TIMELINE)
    return True


def _canonical_tool_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    normalized = value.strip().replace("-", "_").lower()
    for separator in ("__", ".", "/"):
        if separator in normalized:
            normalized = normalized.split(separator)[-1]
    aliases = {"sendinput": "send_input", "spawnagent": "spawn_agent", "closeagent": "close_agent"}
    return aliases.get(normalized, normalized)


def _safe_tool_target(tool_input: Any) -> List[str]:
    if not isinstance(tool_input, dict):
        return []
    candidates: List[Any] = []
    for key in ("target", "recipient", "agent_id", "agentId", "receiverThreadId"):
        if key in tool_input:
            candidates.append(tool_input.get(key))
    for key in ("targets", "recipients", "agent_ids", "agentIds", "receiverThreadIds"):
        value = tool_input.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    result: List[str] = []
    for value in candidates:
        cleaned = safe_id(value)
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result[:8]


def _remember_spawn_request(snapshot: Dict[str, Any], payload: Dict[str, Any], now: str) -> None:
    tool_input = payload.get("tool_input")
    role_hint = ""
    if isinstance(tool_input, dict):
        for key in ("task_name", "agent_type", "subagent_type", "name"):
            candidate = tool_input.get(key)
            if isinstance(candidate, str) and SAFE_ROLE_HINT.fullmatch(candidate.strip()):
                role_hint = candidate.strip()
                break
    pending = {
        "session_id": safe_id(payload.get("session_id")),
        "turn_id": safe_id(payload.get("turn_id")),
        "tool_use_id": safe_id(payload.get("tool_use_id")),
        "parent_agent_id": safe_id(payload.get("agent_id")) or safe_id(payload.get("session_id")),
        "role_cn": role_cn(role_hint),
        "matched_agent_id": "",
        "observed_at": now,
    }
    if pending["session_id"]:
        if pending["tool_use_id"]:
            _upsert(snapshot["pending_spawns"], "tool_use_id", pending, MAX_PENDING_SPAWNS)
        else:
            snapshot["pending_spawns"].append(pending)
            del snapshot["pending_spawns"][:-MAX_PENDING_SPAWNS]


def _spawn_child_id(tool_response: Any) -> str:
    """Read only an allowlisted exact child identifier from a spawn result."""

    if not isinstance(tool_response, dict):
        return ""
    candidates: List[Any] = [tool_response]
    for key in ("structuredContent", "structured_content", "result", "data"):
        nested = tool_response.get(key)
        if isinstance(nested, dict):
            candidates.append(nested)
    exact_keys = (
        "agent_id",
        "agentId",
        "child_agent_id",
        "childAgentId",
        "child_session_id",
        "childSessionId",
        "agent_thread_id",
        "agentThreadId",
        "thread_id",
        "threadId",
    )
    for candidate in candidates:
        for key in exact_keys:
            value = safe_id(candidate.get(key))
            if value:
                return value
    return ""


def _bind_completed_spawn(snapshot: Dict[str, Any], payload: Dict[str, Any]) -> None:
    """Bind a spawn request only when PostToolUse proves its exact child ID."""

    tool_use_id = safe_id(payload.get("tool_use_id"))
    child_id = _spawn_child_id(payload.get("tool_response"))
    if not tool_use_id or not child_id:
        return
    pending = next(
        (item for item in snapshot.get("pending_spawns", []) if item.get("tool_use_id") == tool_use_id),
        None,
    )
    if not isinstance(pending, dict):
        return
    pending["matched_agent_id"] = child_id

    # SubagentStart may be delivered before the spawn tool's PostToolUse. If
    # that happens, repair only the exact child proved by the tool result.
    agent = next((item for item in snapshot.get("agents", []) if item.get("agent_id") == child_id), None)
    if not isinstance(agent, dict):
        return
    parent_id = pending.get("parent_agent_id") or agent.get("root_thread_id")
    role = pending.get("role_cn") or agent.get("role_cn")
    agent["parent_agent_id"] = parent_id
    agent["display_name"] = role
    agent["role_cn"] = role
    edge = next(
        (item for item in snapshot.get("edges", []) if item.get("edge_id") == "hook:spawn:" + child_id),
        None,
    )
    if isinstance(edge, dict):
        edge["from_agent_id"] = parent_id
    event = next(
        (item for item in snapshot.get("timeline", []) if item.get("event_id") == "hook:spawn:" + child_id),
        None,
    )
    if isinstance(event, dict):
        event["actor_agent_id"] = parent_id


def _tool_failed(tool_response: Any) -> bool:
    if not isinstance(tool_response, dict):
        return False
    return tool_response.get("isError") is True or isinstance(tool_response.get("error"), dict)


def _record_timeline_event(snapshot: Dict[str, Any], event: Dict[str, Any], completed: bool) -> None:
    """Upsert one event, folding only consecutive completed wait polls."""
    timeline = snapshot["timeline"]
    existing = next(
        (item for item in timeline if item.get("event_id") == event.get("event_id")), None
    )
    if isinstance(existing, dict):
        if completed and existing.get("status") in ("completed", "failed"):
            return
        if not completed and existing.get("status") in ("completed", "failed"):
            return
    if event.get("event_type") != "wait" or not completed:
        _upsert(timeline, "event_id", event, MAX_TIMELINE)
        return

    if event.get("status") != "completed":
        _upsert(timeline, "event_id", event, MAX_TIMELINE)
        return

    # Replace this tool call's PreToolUse placeholder before looking for the
    # preceding completed wait. This preserves non-wait ordering and avoids
    # counting Pre/Post as two waits.
    timeline[:] = [item for item in timeline if item.get("event_id") != event.get("event_id")]
    previous = timeline[-1] if timeline else None
    if (
        isinstance(previous, dict)
        and previous.get("event_type") == "wait"
        and previous.get("status") == "completed"
        and previous.get("actor_agent_id") == event.get("actor_agent_id")
        and previous.get("target_agent_ids") == event.get("target_agent_ids")
    ):
        previous["repeat_count"] = int(previous.get("repeat_count") or 1) + 1
        previous["observed_at"] = event.get("observed_at")
        return
    event["repeat_count"] = 1
    _upsert(timeline, "event_id", event, MAX_TIMELINE)


def _activity_for_tool(tool: str) -> Tuple[str, str, str]:
    if tool.startswith("mcp_") or tool.startswith("mcp__"):
        return "integration", "正在调用已连接的集成工具", "已完成一项集成工具调用"
    return TOOL_ACTIVITY.get(tool, ("tool", "正在使用工程工具", "已完成一项工程工具调用"))


def _touch_agent_activity(
    snapshot: Dict[str, Any], payload: Dict[str, Any], tool: str, completed: bool, now: str
) -> bool:
    agent_id = safe_id(payload.get("agent_id"))
    if not agent_id:
        return False
    agent = next((item for item in snapshot["agents"] if item.get("agent_id") == agent_id), None)
    if agent is None:
        return False
    activity_kind, started_summary, completed_summary = _activity_for_tool(tool)
    failed = completed and _tool_failed(payload.get("tool_response"))
    agent["execution_state"] = "working"
    agent["progress_summary"] = completed_summary if completed else started_summary
    agent["current_difficulty"] = (
        "最近一项工程工具调用失败；请在原 Codex 任务核验错误详情"
        if failed
        else "未通过隐私最小化 Hook 报告结构化困难"
    )
    agent["updated_at"] = now
    agent["last_activity_at"] = now
    tool_use_id = safe_id(payload.get("tool_use_id")) or uuid.uuid4().hex
    event = {
        "event_id": "hook:activity:" + tool_use_id,
        "event_type": "activity",
        "activity_kind": activity_kind,
        "actor_agent_id": agent_id,
        "target_agent_ids": [],
        "root_thread_id": agent.get("root_thread_id", ""),
        "status": "failed" if failed else ("completed" if completed else "started"),
        "observed_at": now,
        "summary": agent["progress_summary"],
        "source_quality": "codex_hooks_realtime",
        "source": "codex_hooks",
    }
    _upsert(snapshot["timeline"], "event_id", event, MAX_TIMELINE)
    return True


def _record_permission(snapshot: Dict[str, Any], payload: Dict[str, Any], now: str) -> bool:
    agent_id = safe_id(payload.get("agent_id"))
    if not agent_id:
        return False
    agent = next((item for item in snapshot["agents"] if item.get("agent_id") == agent_id), None)
    if agent is None:
        return False
    agent["execution_state"] = "waiting_on_human"
    agent["progress_summary"] = "正在等待权限审批"
    agent["current_difficulty"] = "继续执行需要人类决定是否授予本次权限"
    agent["updated_at"] = now
    agent["last_activity_at"] = now
    tool_use_id = safe_id(payload.get("tool_use_id")) or uuid.uuid4().hex
    _upsert(
        snapshot["timeline"],
        "event_id",
        {
            "event_id": "hook:permission:" + tool_use_id,
            "event_type": "permission",
            "actor_agent_id": agent_id,
            "target_agent_ids": [],
            "root_thread_id": agent.get("root_thread_id", ""),
            "status": "waiting",
            "observed_at": now,
            "summary": "子 Agent 正在等待权限审批",
            "source_quality": "codex_hooks_realtime",
            "source": "codex_hooks",
        },
        MAX_TIMELINE,
    )
    return True


def _record_tool(snapshot: Dict[str, Any], payload: Dict[str, Any], completed: bool, now: str) -> bool:
    tool = _canonical_tool_name(payload.get("tool_name"))
    if tool in ("spawn_agent", "agent"):
        if not completed:
            _remember_spawn_request(snapshot, payload, now)
        elif not _tool_failed(payload.get("tool_response")):
            _bind_completed_spawn(snapshot, payload)
        return True
    action = TOOL_ACTIONS.get(tool)
    if action is None:
        return _touch_agent_activity(snapshot, payload, tool, completed, now)
    event_type, summary = action
    session_id = safe_id(payload.get("session_id"))
    actor_id = safe_id(payload.get("agent_id")) or session_id
    turn_id = safe_id(payload.get("turn_id"))
    tool_use_id = safe_id(payload.get("tool_use_id")) or uuid.uuid4().hex
    targets = _safe_tool_target(payload.get("tool_input"))
    status = "started"
    if completed:
        status = "failed" if _tool_failed(payload.get("tool_response")) else "completed"
    event = {
        "event_id": "hook:tool:" + tool_use_id,
        "event_type": event_type,
        "actor_agent_id": actor_id,
        "target_agent_ids": targets,
        "root_thread_id": session_id,
        "status": status,
        "observed_at": now,
        "summary": summary,
        "source_quality": "codex_hooks_realtime",
        "source": "codex_hooks",
    }
    _record_timeline_event(snapshot, event, completed)
    if event_type != "wait":
        edge = {
            "edge_id": event["event_id"],
            "edge_type": event_type,
            "action": tool,
            "from_agent_id": actor_id,
            "to_agent_ids": targets,
            "root_thread_id": session_id,
            "status": status,
            "observed_at": now,
            "summary": summary,
            "source_quality": "codex_hooks_realtime",
            "source": "codex_hooks",
        }
        _upsert(snapshot["edges"], "edge_id", edge, MAX_EDGES)
    if event_type == "close" and completed:
        for agent in snapshot["agents"]:
            if agent.get("agent_id") in targets:
                agent["execution_state"] = "interrupted"
                agent["progress_summary"] = "Codex Hook 已确认上级中断该 Agent"
                agent["current_difficulty"] = "中断原因需在原任务核验"
                agent["updated_at"] = now
                agent["last_activity_at"] = now
    del turn_id  # validated for schema defense; intentionally not persisted
    return True


def _record_session_end(snapshot: Dict[str, Any], payload: Dict[str, Any], now: str) -> bool:
    session_id = safe_id(payload.get("session_id"))
    if not session_id:
        return False
    for agent in snapshot["agents"]:
        if agent.get("root_thread_id") == session_id and agent.get("execution_state") in (
            "working",
            "waiting_on_human",
        ):
            agent["execution_state"] = "stopped"
            agent["progress_summary"] = "主任务会话已结束；该 Agent 不再计入工作中"
            agent["current_difficulty"] = "最终工作结果需在原 Codex 任务核验"
            agent["updated_at"] = now
            agent["last_activity_at"] = now
    _record_run(snapshot, session_id, "idle", now)
    _upsert(
        snapshot["timeline"],
        "event_id",
        {
            "event_id": "hook:session-end:" + session_id,
            "event_type": "session_end",
            "actor_agent_id": session_id,
            "target_agent_ids": [],
            "root_thread_id": session_id,
            "status": "completed",
            "observed_at": now,
            "summary": "Codex 主任务会话已结束",
            "source_quality": "codex_hooks_realtime",
            "source": "codex_hooks",
        },
        MAX_TIMELINE,
    )
    return True


def _start_session(snapshot: Dict[str, Any], session_id: str, source: str, now: str) -> None:
    """Start a fresh monitoring generation for one root session.

    A restarted or resumed Codex session must not inherit a previously
    unfinished child as live evidence. Settled children and immutable
    lifecycle events remain available for the timeline.
    """

    if source == "compact":
        # Context compaction continues the same live session. It is not a new
        # execution generation and must never terminate active children.
        _record_run(snapshot, session_id, "active", now)
        return

    for agent in snapshot["agents"]:
        if agent.get("root_thread_id") != session_id:
            continue
        if agent.get("execution_state") in ("working", "waiting_on_human"):
            agent["execution_state"] = "stopped"
            agent["progress_summary"] = "新的主任务会话已开始；旧运行状态已隔离"
            agent["current_difficulty"] = "旧会话未提供可验证的完成结果"
            agent["updated_at"] = now
            agent["last_activity_at"] = now
    snapshot["pending_spawns"] = [
        item for item in snapshot["pending_spawns"] if item.get("session_id") != session_id
    ]
    _record_run(snapshot, session_id, "active", now)


def _run_has_ended(snapshot: Dict[str, Any], session_id: str) -> bool:
    if not session_id:
        return False
    run = next(
        (item for item in snapshot["runs"] if item.get("root_thread_id") == session_id), None
    )
    return isinstance(run, dict) and run.get("execution_state") == "idle"


def project_event(snapshot: Dict[str, Any], payload: Dict[str, Any], now: Optional[str] = None) -> bool:
    event_name = payload.get("hook_event_name")
    observed_at = safe_time(now)
    session_id = safe_id(payload.get("session_id"))
    if event_name not in ("SessionStart", "SessionEnd") and _run_has_ended(snapshot, session_id):
        # Hooks may finish concurrently. A late child/tool event must never
        # resurrect a root session after its exact SessionEnd evidence or
        # rewrite another still-active root's bridge health.
        return False
    handled = False
    if event_name == "SubagentStart":
        handled = _record_subagent(snapshot, payload, True, observed_at)
    elif event_name == "SubagentStop":
        handled = _record_subagent(snapshot, payload, False, observed_at)
    elif event_name == "PreToolUse":
        handled = _record_tool(snapshot, payload, False, observed_at)
    elif event_name == "PostToolUse":
        handled = _record_tool(snapshot, payload, True, observed_at)
    elif event_name == "PermissionRequest":
        handled = _record_permission(snapshot, payload, observed_at)
    elif event_name == "SessionStart":
        session_id = safe_id(payload.get("session_id"))
        handled = bool(session_id)
        if handled:
            raw_source = payload.get("source")
            source = raw_source if raw_source in ("startup", "resume", "clear", "compact") else "startup"
            _start_session(snapshot, session_id, source, observed_at)
            summary = {
                "startup": "Codex 主任务会话已开始",
                "resume": "Codex 主任务会话已恢复",
                "clear": "Codex 主任务已开始新的会话",
                "compact": "Codex 完成上下文压缩并继续当前会话",
            }[source]
            _upsert(
                snapshot["timeline"],
                "event_id",
                {
                    "event_id": "hook:session-start:" + session_id + ":" + source,
                    "event_type": "session_start",
                    "actor_agent_id": session_id,
                    "target_agent_ids": [],
                    "root_thread_id": session_id,
                    "status": "started",
                    "observed_at": observed_at,
                    "summary": summary,
                    "source_quality": "codex_hooks_realtime",
                    "source": "codex_hooks",
                },
                MAX_TIMELINE,
            )
    elif event_name == "SessionEnd":
        handled = _record_session_end(snapshot, payload, observed_at)
    if not handled:
        return False
    if event_name not in ("SessionStart", "SessionEnd"):
        _record_run(snapshot, session_id, "active", observed_at)
    active_runs, run_count = _run_counts(snapshot)
    snapshot["protocol_version"] = PROTOCOL_VERSION
    snapshot["generated_at"] = observed_at
    snapshot["bridge"] = {
        "state": "active" if active_runs else "idle",
        "source": "codex_hooks",
        "root_thread_id": session_id,
        "last_success_at": observed_at,
        "error": "",
        "active_runs": active_runs,
        "run_count": run_count,
    }
    return True


def process_payload(payload: Dict[str, Any], path: Path, workspace_id: str = "", now: Optional[str] = None) -> bool:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = path.with_suffix(path.suffix + ".lock")
    with lock_path.open("a+", encoding="utf-8") as lock:
        os.chmod(lock_path, 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        snapshot = _load_snapshot(path, workspace_id)
        if not project_event(snapshot, payload, now=now):
            return False
        _atomic_write(path, snapshot)
    return True


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=default_output_path())
    parser.add_argument("--kandev-workspace-id", default="")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        return 0
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return 0
    if isinstance(payload, dict):
        process_payload(payload, args.output, args.kandev_workspace_id)
    # SubagentStop requires JSON stdout; an empty object is valid and never
    # injects model-visible context for any supported event.
    sys.stdout.write("{}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
