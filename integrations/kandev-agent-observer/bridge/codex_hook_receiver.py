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


PROTOCOL_VERSION = "codex-hooks-observer/v1"
MAX_INPUT_BYTES = 1 << 20
MAX_AGENTS = 128
MAX_EDGES = 256
MAX_TIMELINE = 512
MAX_PENDING_SPAWNS = 64

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
    ):
        values = loaded.get(key)
        if isinstance(values, list):
            result[key] = [item for item in values[-limit:] if isinstance(item, dict)]
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


def _role_for_start(snapshot: Dict[str, Any], session_id: str, agent_type: Any, agent_id: str) -> str:
    direct = role_cn(agent_type)
    pending = snapshot.get("pending_spawns", [])
    for item in reversed(pending):
        if item.get("session_id") == session_id and not item.get("matched_agent_id"):
            item["matched_agent_id"] = agent_id
            return item.get("role_cn") or direct
    return direct


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
    role = (existing or {}).get("role_cn") or _role_for_start(
        snapshot, session_id, payload.get("agent_type"), agent_id
    )
    agent = {
        "agent_id": agent_id,
        "parent_agent_id": session_id,
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
            "from_agent_id": session_id,
            "to_agent_ids": [agent_id],
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
        "actor_agent_id": session_id,
        "target_agent_ids": [agent_id],
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
        "role_cn": role_cn(role_hint),
        "matched_agent_id": "",
        "observed_at": now,
    }
    if pending["session_id"]:
        snapshot["pending_spawns"].append(pending)
        del snapshot["pending_spawns"][:-MAX_PENDING_SPAWNS]


def _tool_failed(tool_response: Any) -> bool:
    if not isinstance(tool_response, dict):
        return False
    return tool_response.get("isError") is True or isinstance(tool_response.get("error"), dict)


def _record_timeline_event(snapshot: Dict[str, Any], event: Dict[str, Any], completed: bool) -> None:
    """Upsert one event, folding only consecutive completed wait polls."""
    timeline = snapshot["timeline"]
    if event.get("event_type") != "wait" or not completed:
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


def _record_tool(snapshot: Dict[str, Any], payload: Dict[str, Any], completed: bool, now: str) -> bool:
    tool = _canonical_tool_name(payload.get("tool_name"))
    if tool in ("spawn_agent", "agent"):
        if not completed:
            _remember_spawn_request(snapshot, payload, now)
        return True
    action = TOOL_ACTIONS.get(tool)
    if action is None:
        return False
    event_type, summary = action
    session_id = safe_id(payload.get("session_id"))
    turn_id = safe_id(payload.get("turn_id"))
    tool_use_id = safe_id(payload.get("tool_use_id")) or uuid.uuid4().hex
    targets = _safe_tool_target(payload.get("tool_input"))
    status = "started"
    if completed:
        status = "failed" if _tool_failed(payload.get("tool_response")) else "completed"
    event = {
        "event_id": "hook:tool:" + tool_use_id,
        "event_type": event_type,
        "actor_agent_id": session_id,
        "target_agent_ids": targets,
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
            "from_agent_id": session_id,
            "to_agent_ids": targets,
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


def project_event(snapshot: Dict[str, Any], payload: Dict[str, Any], now: Optional[str] = None) -> bool:
    event_name = payload.get("hook_event_name")
    observed_at = safe_time(now)
    handled = False
    if event_name == "SubagentStart":
        handled = _record_subagent(snapshot, payload, True, observed_at)
    elif event_name == "SubagentStop":
        handled = _record_subagent(snapshot, payload, False, observed_at)
    elif event_name == "PreToolUse":
        handled = _record_tool(snapshot, payload, False, observed_at)
    elif event_name == "PostToolUse":
        handled = _record_tool(snapshot, payload, True, observed_at)
    elif event_name == "SessionStart":
        handled = bool(safe_id(payload.get("session_id")))
    if not handled:
        return False
    session_id = safe_id(payload.get("session_id"))
    snapshot["protocol_version"] = PROTOCOL_VERSION
    snapshot["generated_at"] = observed_at
    snapshot["bridge"] = {
        "state": "ready",
        "source": "codex_hooks",
        "root_thread_id": session_id,
        "last_success_at": observed_at,
        "error": "",
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
