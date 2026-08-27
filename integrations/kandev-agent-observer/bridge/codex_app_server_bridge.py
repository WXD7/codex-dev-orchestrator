#!/usr/bin/env python3
"""Export a sanitized Codex persisted-history tree for Kandev.

The bridge is deliberately read-only.  It uses the released ``codex app-server``
JSON-RPC contract, never resumes a thread, never starts a turn, and never writes
back to Codex or Kandev.  Its only output is one bounded, atomically replaced
JSON snapshot in the observer plugin's private data directory.  A separately
launched app-server does not share Codex Desktop's in-memory runtime state, so
this bridge intentionally reports history only.  Live lifecycle state comes
from ``codex_hook_receiver.py``.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PROTOCOL_VERSION = "codex-app-server-history/v1"
DEFAULT_INTERVAL_SECONDS = 6.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 20.0
MAX_AGENTS = 64
MAX_TIMELINE_EVENTS = 256
MAX_TEXT_RUNES = 240

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|auth[_ -]?token|token|password|secret|密钥|密码)\b"
    r"\s*(?::|=|is\b|是|为)\s*[\"']?[^,;，；\n\r]{1,240}"
)
SECRET_TOKEN = re.compile(r"\b(?:sk|ds)-[A-Za-z0-9_-]{8,}\b")
BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{8,}=*")

ROLE_LABELS = (
    (("intent", "requirement", "clarif", "意图", "需求确认", "需求检查"), "意图检查员"),
    (("research", "explor", "scout", "调研", "技术探索", "框架比较"), "技术调研员"),
    (("review", "critic", "audit", "judge", "评审", "审查", "裁决"), "独立审查员"),
    (("test", "qa", "verif", "测试", "验证", "验收"), "测试验证员"),
    (("security", "safety", "安全", "密钥", "脱敏"), "安全审查员"),
    (("architect", "design", "架构", "方案设计"), "架构设计员"),
    (("coordinat", "orchestrat", "manager", "协同", "调度", "监督"), "协同调度员"),
    (("implement", "worker", "developer", "coder", "default", "实现", "开发", "修复"), "实现智能体"),
)

ACTION_LABELS = {
    "spawnAgent": ("spawn", "创建子 Agent"),
    "sendInput": ("correction", "上级向子 Agent 发送纠偏或补充要求"),
    "resumeAgent": ("resume", "恢复子 Agent"),
    "wait": ("wait", "等待子 Agent 结果"),
    "closeAgent": ("close", "关闭子 Agent"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_to_rfc3339(value: Any) -> str:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return ""
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_text(value: Any, limit: int = MAX_TEXT_RUNES) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split())
    text = SECRET_ASSIGNMENT.sub(lambda match: match.group(1) + "=[已脱敏]", text)
    text = SECRET_TOKEN.sub("[已脱敏]", text)
    text = BEARER_TOKEN.sub("Bearer [已脱敏]", text)
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "…"
    return text


def role_cn(role: Any) -> str:
    normalized = sanitize_text(role, 64).lower()
    for keys, label in ROLE_LABELS:
        if any(key in normalized for key in keys):
            return label
    return "执行智能体"


def status_from_thread(thread: Dict[str, Any]) -> str:
    thread_status = thread.get("status")
    if isinstance(thread_status, dict):
        status_type = thread_status.get("type")
        if status_type == "active":
            return "working"
        if status_type == "systemError":
            return "failed"
        if status_type == "idle":
            return "finished"

    turns = thread.get("turns")
    if isinstance(turns, list) and turns:
        last = turns[-1] if isinstance(turns[-1], dict) else {}
        turn_status = last.get("status")
        if turn_status == "inProgress":
            return "working"
        if turn_status == "failed":
            return "failed"
        if turn_status == "interrupted":
            return "interrupted"
        if turn_status == "completed":
            return "finished"
    return "not_loaded"


def state_message(_thread: Dict[str, Any]) -> str:
    return "来自 Codex 持久化历史；不代表当前实时运行状态"


def minimal_subprocess_env(source: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Pass only runtime essentials to app-server, never API keys or tokens."""
    source = source or dict(os.environ)
    allowed = {
        "HOME",
        "PATH",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "NO_COLOR",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
    return {key: value for key, value in source.items() if key in allowed}


class AppServerError(RuntimeError):
    """Raised when the read-only app-server request path fails."""


class AppServerClient:
    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS,
    ) -> None:
        if not command:
            raise ValueError("app-server command is required")
        self._command = list(command)
        self._timeout_seconds = timeout_seconds
        self._responses: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._pending: Dict[Any, Dict[str, Any]] = {}
        self._next_id = 1
        self._closed = False
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            shell=False,
            env=minimal_subprocess_env(),
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                value = json.loads(line)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and "id" in value:
                self._responses.put(value)

    def _write(self, value: Dict[str, Any]) -> None:
        if self._closed or self._process.poll() is not None:
            raise AppServerError("codex app-server is not running")
        assert self._process.stdin is not None
        try:
            self._process.stdin.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AppServerError("codex app-server connection closed") from exc

    def request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        payload: Dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            payload["params"] = params
        self._write(payload)

        deadline = time.monotonic() + self._timeout_seconds
        while True:
            cached = self._pending.pop(request_id, None)
            if cached is not None:
                response = cached
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError("codex app-server request timed out: " + method)
            try:
                candidate = self._responses.get(timeout=remaining)
            except queue.Empty as exc:
                raise AppServerError("codex app-server request timed out: " + method) from exc
            candidate_id = candidate.get("id")
            if candidate_id == request_id:
                response = candidate
                break
            self._pending[candidate_id] = candidate

        if "error" in response:
            error = response.get("error")
            if isinstance(error, dict):
                code = sanitize_text(error.get("code"), 80)
                message = sanitize_text(error.get("message"), 180)
                detail = ": ".join(part for part in (code, message) if part)
            else:
                detail = sanitize_text(error, 180)
            raise AppServerError(method + " failed" + (": " + detail if detail else ""))
        result = response.get("result")
        if not isinstance(result, dict):
            return {}
        return result

    def initialize(self) -> None:
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "kandev_agent_observer",
                    "title": "Kandev Agent Observer",
                    "version": "0.4.2",
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        self._write({"method": "initialized", "params": {}})

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.terminate()
            self._process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self._process.kill()
            except OSError:
                pass

    def __enter__(self) -> "AppServerClient":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()


def _list_descendants(client: Any, root_thread_id: str) -> List[Dict[str, Any]]:
    descendants: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while len(descendants) < MAX_AGENTS:
        params: Dict[str, Any] = {
            "ancestorThreadId": root_thread_id,
            "limit": min(100, MAX_AGENTS - len(descendants)),
            "sortKey": "created_at",
            "sortDirection": "asc",
        }
        if cursor:
            params["cursor"] = cursor
        result = client.request("thread/list", params)
        data = result.get("data")
        if isinstance(data, list):
            descendants.extend(item for item in data if isinstance(item, dict))
        next_cursor = result.get("nextCursor")
        if not isinstance(next_cursor, str) or not next_cursor:
            break
        cursor = next_cursor
    return descendants[:MAX_AGENTS]


def _read_thread(client: Any, thread_id: str) -> Dict[str, Any]:
    result = client.request("thread/read", {"threadId": thread_id, "includeTurns": True})
    thread = result.get("thread")
    return thread if isinstance(thread, dict) else {}


def _walk_items(thread: Dict[str, Any]) -> Iterable[Tuple[Dict[str, Any], Dict[str, Any]]]:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        items = turn.get("items")
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                yield turn, item


def _collaboration_projection(
    threads: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    edges: List[Dict[str, Any]] = []
    timeline: List[Dict[str, Any]] = []
    seen: set = set()

    for thread in threads:
        for turn, item in _walk_items(thread):
            item_type = item.get("type")
            observed_at = epoch_to_rfc3339(turn.get("completedAt") or turn.get("startedAt"))
            if item_type == "collabAgentToolCall":
                tool = item.get("tool")
                edge_type, summary = ACTION_LABELS.get(str(tool), ("interaction", "Agent 协作事件"))
                sender = sanitize_text(item.get("senderThreadId"), 80) or str(thread.get("id", ""))
                receivers = item.get("receiverThreadIds")
                receiver_ids = [sanitize_text(value, 80) for value in receivers] if isinstance(receivers, list) else []
                receiver_ids = [value for value in receiver_ids if value]
                event_id = sanitize_text(item.get("id"), 100)
                dedupe = (event_id, edge_type, tuple(receiver_ids))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                edge = {
                    "edge_id": event_id or "collab:" + str(len(edges) + 1),
                    "edge_type": edge_type,
                    "action": sanitize_text(tool, 40),
                    "from_agent_id": sender,
                    "to_agent_ids": receiver_ids,
                    "status": sanitize_text(item.get("status"), 40),
                    "observed_at": observed_at,
                    "summary": summary,
                    "source_quality": "codex_app_server_history",
                }
                # Waiting is a lifecycle observation, not a parent/child or
                # correction relationship in the DAG.
                if edge_type != "wait":
                    edges.append(edge)
                timeline.append(
                    {
                        "event_id": edge["edge_id"],
                        "event_type": edge_type,
                        "actor_agent_id": sender,
                        "target_agent_ids": receiver_ids,
                        "status": edge["status"],
                        "observed_at": observed_at,
                        "summary": summary,
                            "source_quality": "codex_app_server_history",
                        }
                    )
            elif item_type == "subAgentActivity":
                event_id = sanitize_text(item.get("id"), 100)
                agent_id = sanitize_text(item.get("agentThreadId"), 80)
                kind = sanitize_text(item.get("kind"), 40)
                dedupe = (event_id, "activity", agent_id, kind)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                timeline.append(
                    {
                        "event_id": event_id or "activity:" + str(len(timeline) + 1),
                        "event_type": "activity",
                        "actor_agent_id": agent_id,
                        "target_agent_ids": [],
                        "status": kind,
                        "observed_at": observed_at,
                        "summary": {
                            "started": "子 Agent 开始工作",
                            "interacted": "子 Agent 与上级发生交互",
                            "interrupted": "子 Agent 工作被中断",
                        }.get(kind, "子 Agent 活动"),
                        "source_quality": "codex_app_server_history",
                    }
                )

    # Python's sort is stable, so events with the same turn timestamp retain the
    # exact collaboration order emitted by app-server.
    edges.sort(key=lambda item: item.get("observed_at", ""))
    timeline.sort(key=lambda item: item.get("observed_at", ""))
    compacted: List[Dict[str, Any]] = []
    compacted_indexes: Dict[Tuple[Any, ...], int] = {}
    for event in timeline:
        event_type = event.get("event_type")
        if event_type not in ("wait", "activity"):
            compacted.append(event)
            continue
        key = (
            event_type,
            event.get("actor_agent_id", ""),
            tuple(event.get("target_agent_ids", [])),
            event.get("status", "") if event_type == "activity" else "",
        )
        previous_index = compacted_indexes.get(key)
        if previous_index is None:
            copied = dict(event)
            copied["repeat_count"] = 1
            compacted_indexes[key] = len(compacted)
            compacted.append(copied)
            continue
        copied = dict(event)
        copied["repeat_count"] = int(compacted[previous_index].get("repeat_count", 1)) + 1
        compacted[previous_index] = copied
    timeline = sorted(compacted, key=lambda item: item.get("observed_at", ""))[-MAX_TIMELINE_EVENTS:]
    return edges[-MAX_TIMELINE_EVENTS:], timeline


def collect_snapshot(
    client: Any,
    root_thread_id: str,
    cwd: str = "",
    kandev_workspace_id: str = "",
) -> Dict[str, Any]:
    descendants = _list_descendants(client, root_thread_id)
    metadata_by_id = {
        str(item.get("id")): item for item in descendants if isinstance(item.get("id"), str)
    }

    root = _read_thread(client, root_thread_id)
    if not root:
        raise AppServerError("root thread was not found")
    detailed: List[Dict[str, Any]] = [root]
    for thread_id in list(metadata_by_id):
        detail = _read_thread(client, thread_id)
        if detail:
            merged = dict(metadata_by_id[thread_id])
            merged.update(detail)
            metadata_by_id[thread_id] = merged
            detailed.append(merged)

    edges, timeline = _collaboration_projection(detailed)
    ordered = sorted(
        metadata_by_id.values(),
        key=lambda item: (item.get("createdAt") or 0, str(item.get("id", ""))),
    )
    role_counts: Dict[str, int] = {}
    agents: List[Dict[str, Any]] = []
    for thread in ordered:
        role = role_cn(thread.get("agentRole"))
        role_counts[role] = role_counts.get(role, 0) + 1
        display_name = role
        if role_counts[role] > 1:
            display_name += " " + str(role_counts[role])
        thread_id = sanitize_text(thread.get("id"), 80)
        agents.append(
            {
                "agent_id": thread_id,
                "parent_agent_id": sanitize_text(thread.get("parentThreadId"), 80) or root_thread_id,
                "display_name": display_name,
                "nickname": sanitize_text(thread.get("agentNickname"), 48),
                "role": sanitize_text(thread.get("agentRole"), 64),
                "role_cn": role,
                "execution_state": "historical",
                "progress_summary": state_message(thread),
                "current_difficulty": "历史通道不判断当前困难",
                "created_at": epoch_to_rfc3339(thread.get("createdAt")),
                "updated_at": epoch_to_rfc3339(thread.get("updatedAt")),
                "last_activity_at": epoch_to_rfc3339(thread.get("updatedAt")),
                "source_quality": "codex_app_server_history",
                "source": "codex_app_server_history",
            }
        )

    generated_at = utc_now()
    return {
        "protocol_version": PROTOCOL_VERSION,
        "generated_at": generated_at,
        "kandev_workspace_id": sanitize_text(kandev_workspace_id, 80),
        "bridge": {
            "state": "history_synced",
            "source": "codex_app_server_history",
            "protocol_version": PROTOCOL_VERSION,
            "root_thread_id": sanitize_text(root_thread_id, 80),
            "last_success_at": generated_at,
            "error": "",
        },
        "root": {
            "thread_id": sanitize_text(root_thread_id, 80),
            "execution_state": "historical",
            "updated_at": epoch_to_rfc3339(root.get("updatedAt")),
        },
        "agents": agents,
        "edges": edges,
        "timeline": timeline,
    }


def default_output_path() -> Path:
    override = os.environ.get("KANDEV_AGENT_OBSERVER_EVENTS")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kandev" / "plugins" / "ai-delivery-agent-observer" / "data" / "codex-app-snapshot.json"


def atomic_write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".codex-observer-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temporary), str(path))


def failure_snapshot(path: Path, root_thread_id: str, cwd: str, error: Exception) -> Dict[str, Any]:
    previous: Dict[str, Any] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            previous = loaded
    except (OSError, ValueError):
        pass
    generated_at = utc_now()
    previous["protocol_version"] = PROTOCOL_VERSION
    previous["generated_at"] = generated_at
    previous.setdefault("agents", [])
    previous.setdefault("edges", [])
    previous.setdefault("timeline", [])
    prior_bridge = previous.get("bridge") if isinstance(previous.get("bridge"), dict) else {}
    previous["bridge"] = {
        "state": "stale" if previous.get("agents") else "unavailable",
        "source": "codex_app_server_history",
        "protocol_version": PROTOCOL_VERSION,
        "root_thread_id": sanitize_text(root_thread_id, 80),
        "last_success_at": sanitize_text(prior_bridge.get("last_success_at"), 60),
        "error": sanitize_text(str(error), 180),
    }
    return previous


def resolve_codex_command(value: Optional[str], proxy_socket: Optional[str]) -> List[str]:
    executable = value or os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not executable:
        app_binary = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
        if app_binary.is_file():
            executable = str(app_binary)
    if not executable:
        raise SystemExit("cannot find codex; pass --codex-bin or set CODEX_BIN")
    if proxy_socket:
        return [executable, "app-server", "proxy", "--sock", proxy_socket]
    return [executable, "app-server", "--stdio"]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-thread-id", required=True, help="Codex root thread whose descendants should be observed")
    parser.add_argument("--cwd", default="", help="Deprecated compatibility argument; never persisted")
    parser.add_argument("--kandev-workspace-id", default="", help="Only show the snapshot in this Kandev workspace")
    parser.add_argument("--output", type=Path, default=default_output_path())
    parser.add_argument("--codex-bin")
    parser.add_argument("--proxy-socket", help="Use a released app-server daemon socket through `codex app-server proxy`")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--request-timeout", type=float, default=DEFAULT_REQUEST_TIMEOUT_SECONDS)
    parser.add_argument("--once", action="store_true")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    command = resolve_codex_command(args.codex_bin, args.proxy_socket)
    output = args.output.expanduser().resolve()
    while True:
        try:
            with AppServerClient(command, timeout_seconds=args.request_timeout) as client:
                client.initialize()
                while True:
                    snapshot = collect_snapshot(
                        client,
                        args.root_thread_id,
                        cwd=args.cwd,
                        kandev_workspace_id=args.kandev_workspace_id,
                    )
                    atomic_write_json(output, snapshot)
                    if args.once:
                        return 0
                    time.sleep(max(1.0, args.interval))
        except KeyboardInterrupt:
            return 130
        except Exception as exc:  # monitoring must fail open and retain last good data
            atomic_write_json(output, failure_snapshot(output, args.root_thread_id, args.cwd, exc))
            if args.once:
                return 1
            time.sleep(max(2.0, args.interval))


def main() -> None:
    raise SystemExit(run(parse_args()))


if __name__ == "__main__":
    main()
