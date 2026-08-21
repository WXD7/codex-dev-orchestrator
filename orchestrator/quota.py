"""Quota observation and deterministic scheduling policy.

The orchestrator never reads OAuth tokens and never calls a model API.  Codex
quota is read through the locally authenticated App Server.  Claude quota is
learned from the rate-limit events emitted by the locally authenticated CLI and
cached without credentials so the next task can be scheduled before it starts.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PRIVATE_ENV_KEYS = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "CLAUDE_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN",
)


@dataclass(frozen=True)
class QuotaWindow:
    name: str
    used_percent: float
    resets_at: Optional[int] = None
    window_minutes: Optional[int] = None
    label: str = ""
    reached: bool = False

    @property
    def remaining_percent(self) -> float:
        return max(0.0, min(100.0, 100.0 - self.used_percent))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label or self.name,
            "used_percent": round(self.used_percent, 2),
            "remaining_percent": round(self.remaining_percent, 2),
            "resets_at": self.resets_at,
            "window_minutes": self.window_minutes,
            "reached": self.reached,
        }

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "QuotaWindow":
        return cls(
            name=str(value.get("name", "quota")),
            label=str(value.get("label", "")),
            used_percent=_percent(value.get("used_percent")),
            resets_at=_integer(value.get("resets_at")),
            window_minutes=_integer(value.get("window_minutes")),
            reached=bool(value.get("reached", False)),
        )


@dataclass(frozen=True)
class QuotaSnapshot:
    executor: str
    plan: str = ""
    windows: Tuple[QuotaWindow, ...] = ()
    observed_at: int = field(default_factory=lambda: int(time.time()))
    source: str = "unknown"
    confidence: str = "unknown"
    error: str = ""
    buckets: Tuple[Dict[str, Any], ...] = ()

    @property
    def observed(self) -> bool:
        return bool(self.windows)

    @property
    def limiting_window(self) -> Optional[QuotaWindow]:
        if not self.windows:
            return None
        return max(self.windows, key=lambda item: item.used_percent)

    @property
    def remaining_percent(self) -> Optional[float]:
        window = self.limiting_window
        return window.remaining_percent if window else None

    @property
    def reset_at(self) -> Optional[int]:
        window = self.limiting_window
        return window.resets_at if window else None

    @property
    def reached(self) -> bool:
        return any(window.reached or window.used_percent >= 100 for window in self.windows)

    def to_dict(self) -> Dict[str, Any]:
        limiting = self.limiting_window
        return {
            "executor": self.executor,
            "plan": self.plan,
            "observed": self.observed,
            "observed_at": self.observed_at,
            "source": self.source,
            "confidence": self.confidence,
            "error": self.error,
            "remaining_percent": (
                round(self.remaining_percent, 2)
                if self.remaining_percent is not None
                else None
            ),
            "reset_at": self.reset_at,
            "limiting_window": limiting.to_dict() if limiting else None,
            "windows": [window.to_dict() for window in self.windows],
            "buckets": list(self.buckets),
        }

    @classmethod
    def unknown(cls, executor: str, error: str = "额度尚未观测") -> "QuotaSnapshot":
        return cls(executor=executor, error=error)

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "QuotaSnapshot":
        windows = tuple(
            QuotaWindow.from_dict(item)
            for item in value.get("windows", [])
            if isinstance(item, dict)
        )
        buckets = tuple(
            item for item in value.get("buckets", []) if isinstance(item, dict)
        )
        return cls(
            executor=str(value.get("executor", "")),
            plan=str(value.get("plan", "")),
            windows=windows,
            observed_at=_integer(value.get("observed_at")) or int(time.time()),
            source=str(value.get("source", "unknown")),
            confidence=str(value.get("confidence", "unknown")),
            error=str(value.get("error", "")),
            buckets=buckets,
        )


@dataclass(frozen=True)
class SchedulingDecision:
    executor: str
    model: str
    model_tier: str
    mode: str
    reason: str
    quota: QuotaSnapshot
    score: float
    blocked: bool = False
    defer_until: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "executor": self.executor,
            "model": self.model,
            "model_tier": self.model_tier,
            "mode": self.mode,
            "reason": self.reason,
            "score": round(self.score, 2),
            "blocked": self.blocked,
            "defer_until": self.defer_until,
            "quota": self.quota.to_dict(),
        }


def quota_mode(snapshot: QuotaSnapshot, now: Optional[int] = None) -> str:
    """Turn a provider-specific snapshot into a stable policy band."""
    if not snapshot.observed:
        return "cautious"
    if snapshot.reached:
        return "blocked"
    remaining = snapshot.remaining_percent or 0.0
    current = int(now or time.time())
    seconds_to_reset = (
        max(0, snapshot.reset_at - current) if snapshot.reset_at else None
    )

    if remaining >= 65:
        mode = "generous"
    elif remaining >= 35:
        mode = "balanced"
    elif remaining >= 15:
        mode = "cautious"
    else:
        mode = "reserve"

    # A nearly finished window is less precious than the same remaining share
    # at the beginning of a weekly window.  Upgrade by one band, never past
    # generous, when a refresh is close enough to be useful to the task queue.
    if seconds_to_reset is not None and seconds_to_reset <= 30 * 60:
        mode = {
            "reserve": "cautious",
            "cautious": "balanced",
            "balanced": "generous",
        }.get(mode, mode)
    return mode


def quota_score(snapshot: QuotaSnapshot, preferred: bool = False) -> float:
    """Comparable score used only between ready local executors."""
    if not snapshot.observed:
        return 25.0 + (5.0 if preferred else 0.0)
    if snapshot.reached:
        return -1000.0
    score = float(snapshot.remaining_percent or 0.0)
    now = int(time.time())
    if snapshot.reset_at:
        seconds = max(0, snapshot.reset_at - now)
        if seconds <= 30 * 60:
            score += 18
        elif seconds <= 2 * 60 * 60:
            score += 8
    window = snapshot.limiting_window
    if window and (window.window_minutes or 0) >= 7 * 24 * 60:
        if snapshot.reset_at and snapshot.reset_at - now > 2 * 24 * 60 * 60:
            score -= 8
    if preferred:
        score += 3
    return score


def choose_model_tier(
    snapshot: QuotaSnapshot, role: str, priority: int = 50
) -> str:
    tiers = ("economy", "balanced", "high")
    base = {
        "coordinator": 2,
        "planner": 2,
        "reviewer": 2,
        "implementer": 1,
        "qa": 0,
    }.get(role, 1)
    mode = quota_mode(snapshot)
    adjustment = {
        "generous": 1,
        "balanced": 0,
        "cautious": -1,
        "reserve": -2,
        "blocked": -2,
    }.get(mode, -1)
    if int(priority) >= 80:
        base += 1
    return tiers[max(0, min(2, base + adjustment))]


def decision_reason(snapshot: QuotaSnapshot, mode: str) -> str:
    if not snapshot.observed:
        return "没有可验证的实时额度，采用谨慎档并保留降级空间"
    remaining = snapshot.remaining_percent or 0.0
    reset = "刷新时间未知"
    if snapshot.reset_at:
        seconds = max(0, snapshot.reset_at - int(time.time()))
        reset = "约 %.1f 小时后刷新" % (seconds / 3600.0)
    labels = {
        "generous": "额度充裕",
        "balanced": "额度适中",
        "cautious": "额度偏紧",
        "reserve": "仅保留关键任务",
        "blocked": "额度已触顶",
    }
    return "%s：剩余 %.0f%%，%s" % (labels.get(mode, mode), remaining, reset)


class CodexQuotaProbe:
    """Read ChatGPT plan limits from the local Codex App Server."""

    def __init__(self, binary: str, ttl_seconds: int = 60, timeout_seconds: int = 20):
        self.binary = binary
        self.ttl_seconds = max(5, int(ttl_seconds))
        self.timeout_seconds = max(5, int(timeout_seconds))
        self._cache: Optional[QuotaSnapshot] = None
        self._lock = threading.Lock()

    def read(self, force: bool = False) -> QuotaSnapshot:
        with self._lock:
            now = int(time.time())
            if (
                not force
                and self._cache
                and now - self._cache.observed_at < self.ttl_seconds
            ):
                return self._cache
            try:
                self._cache = self._query()
            except Exception as exc:
                self._cache = QuotaSnapshot(
                    executor="codex",
                    observed_at=now,
                    source="codex-app-server",
                    confidence="unknown",
                    error=str(exc) or exc.__class__.__name__,
                )
            return self._cache

    def _query(self) -> QuotaSnapshot:
        environment = dict(os.environ)
        for key in PRIVATE_ENV_KEYS:
            environment.pop(key, None)
        environment["NO_COLOR"] = "1"
        process = subprocess.Popen(
            [self.binary, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
            bufsize=1,
        )
        stdout_lines: "queue.Queue[str]" = queue.Queue()
        stderr_lines: List[str] = []

        def pump_stdout() -> None:
            if process.stdout:
                for line in process.stdout:
                    stdout_lines.put(line)

        def pump_stderr() -> None:
            if process.stderr:
                for line in process.stderr:
                    stderr_lines.append(line.rstrip())

        stdout_thread = threading.Thread(target=pump_stdout, daemon=True)
        stderr_thread = threading.Thread(target=pump_stderr, daemon=True)
        stdout_thread.start()
        stderr_thread.start()
        deadline = time.monotonic() + self.timeout_seconds

        def send(message: Dict[str, Any]) -> None:
            if not process.stdin:
                raise RuntimeError("Codex App Server 输入流不可用")
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        def receive(response_id: int) -> Dict[str, Any]:
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                try:
                    line = stdout_lines.get(timeout=min(0.25, max(0.01, remaining)))
                except queue.Empty:
                    if process.poll() is not None:
                        break
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                if (
                    isinstance(message, dict)
                    and message.get("id") == response_id
                    and ("result" in message or "error" in message)
                ):
                    return message
            detail = stderr_lines[-1] if stderr_lines else ""
            raise RuntimeError(
                "Codex App Server 未返回响应 %s%s"
                % (response_id, "：" + detail if detail else "")
            )

        try:
            # Keep stdin open while each response is pending.  In current
            # app-server builds the rate-limit lookup can finish
            # asynchronously; closing stdin immediately after a batch of
            # requests may shut the server down before that result is emitted.
            send(
                {
                    "method": "initialize",
                    "id": 1,
                    "params": {
                        "clientInfo": {
                            "name": "codex_dev_orchestrator",
                            "title": "Codex Dev Orchestrator",
                            "version": "0.2.0",
                        }
                    },
                }
            )
            initialized = receive(1)
            if initialized.get("error"):
                raise RuntimeError("Codex 初始化失败：%s" % initialized["error"])
            send({"method": "initialized", "params": {}})
            send(
                {
                    "method": "account/read",
                    "id": 2,
                    "params": {"refreshToken": False},
                }
            )
            account_message = receive(2)
            send({"method": "account/rateLimits/read", "id": 3})
            limits_message = receive(3)
        finally:
            if process.stdin:
                process.stdin.close()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()

        if account_message.get("error"):
            raise RuntimeError("Codex 账号查询失败：%s" % account_message["error"])
        error = limits_message.get("error")
        if error:
            raise RuntimeError("Codex 额度查询失败：%s" % error)
        if "result" not in limits_message:
            detail = stderr_lines[-1] if stderr_lines else ""
            raise RuntimeError(
                "Codex App Server 未返回额度数据%s"
                % ("：" + detail if detail else "")
            )
        return normalize_codex_snapshot(
            account_message.get("result") or {}, limits_message["result"] or {}
        )


def normalize_codex_snapshot(
    account_result: Dict[str, Any], limits_result: Dict[str, Any]
) -> QuotaSnapshot:
    account = account_result.get("account") or {}
    main = limits_result.get("rateLimits") or {}
    plan = str(account.get("planType") or main.get("planType") or "")
    windows = _codex_windows(main)
    buckets: List[Dict[str, Any]] = []
    by_id = limits_result.get("rateLimitsByLimitId") or {}
    if isinstance(by_id, dict):
        for bucket_id, bucket in by_id.items():
            if not isinstance(bucket, dict):
                continue
            normalized = {
                "id": str(bucket_id),
                "label": str(bucket.get("limitName") or bucket_id),
                "plan": str(bucket.get("planType") or plan),
                "windows": [item.to_dict() for item in _codex_windows(bucket)],
                "reached_type": bucket.get("rateLimitReachedType"),
            }
            buckets.append(normalized)
    return QuotaSnapshot(
        executor="codex",
        plan=plan,
        windows=tuple(windows),
        observed_at=int(time.time()),
        source="codex-app-server",
        confidence="high" if windows else "unknown",
        error="" if windows else "Codex 未返回可用额度窗口",
        buckets=tuple(buckets),
    )


def _codex_windows(bucket: Dict[str, Any]) -> List[QuotaWindow]:
    result: List[QuotaWindow] = []
    reached = bool(bucket.get("rateLimitReachedType"))
    for key, label in (("primary", "主窗口"), ("secondary", "次窗口")):
        window = bucket.get(key)
        if not isinstance(window, dict):
            continue
        used = _percent(window.get("usedPercent"))
        result.append(
            QuotaWindow(
                name=key,
                label=label,
                used_percent=used,
                resets_at=_integer(window.get("resetsAt")),
                window_minutes=_integer(window.get("windowDurationMins")),
                reached=reached or used >= 100,
            )
        )
    return result


class QuotaCache:
    """Credential-free on-disk cache used by event-only quota providers."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def read(self, executor: str) -> QuotaSnapshot:
        with self._lock:
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                snapshot = QuotaSnapshot.from_dict(value)
            except (OSError, ValueError, TypeError):
                return QuotaSnapshot.unknown(
                    executor, "等待该执行器首次返回额度事件"
                )
            return snapshot

    def write(self, snapshot: QuotaSnapshot) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self.path)


def merge_claude_rate_limit(
    previous: QuotaSnapshot, info: Dict[str, Any]
) -> QuotaSnapshot:
    """Merge one Claude RateLimitEvent into the last known snapshot."""
    name = str(info.get("rate_limit_type") or info.get("rateLimitType") or "unknown")
    utilization = info.get("utilization")
    used = _percent(utilization)
    if utilization is not None and 0 <= float(utilization) <= 1:
        used *= 100
    status = str(info.get("status") or "allowed")
    minutes = {
        "five_hour": 5 * 60,
        "seven_day": 7 * 24 * 60,
        "seven_day_opus": 7 * 24 * 60,
        "seven_day_sonnet": 7 * 24 * 60,
    }.get(name)
    window = QuotaWindow(
        name=name,
        label=name.replace("_", " "),
        used_percent=used,
        resets_at=_integer(info.get("resets_at") or info.get("resetsAt")),
        window_minutes=minutes,
        reached=status == "rejected" or used >= 100,
    )
    merged = {item.name: item for item in previous.windows}
    merged[name] = window
    return QuotaSnapshot(
        executor="claude-code",
        plan=previous.plan,
        windows=tuple(sorted(merged.values(), key=lambda item: item.name)),
        observed_at=int(time.time()),
        source="claude-rate-limit-event",
        confidence="high",
    )


def _percent(value: Any) -> float:
    try:
        return max(0.0, min(100.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _integer(value: Any) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
