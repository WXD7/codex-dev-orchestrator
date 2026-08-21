from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class Config:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    max_workers: int = 2
    codex_binary: str = "codex"
    codex_model: str = ""
    codex_model_high: str = "gpt-5.6-sol"
    codex_model_balanced: str = "gpt-5.6-terra"
    codex_model_economy: str = "gpt-5.6-luna"
    claude_binary: str = "claude"
    claude_model: str = ""
    claude_model_high: str = "opus"
    claude_model_balanced: str = "sonnet"
    claude_model_economy: str = "haiku"
    executors: Tuple[str, ...] = ("codex",)
    default_executor: str = "codex"
    cross_review: bool = True
    quota_scheduling: bool = True
    quota_cache_seconds: int = 60
    run_timeout_seconds: int = 3600

    @classmethod
    def from_env(cls, project_root: Path) -> "Config":
        data_dir = Path(
            os.environ.get("ORCH_DATA_DIR", str(project_root / ".data"))
        ).expanduser().resolve()
        executors = tuple(
            name.strip()
            for name in os.environ.get("ORCH_EXECUTORS", "codex").split(",")
            if name.strip()
        ) or ("codex",)
        default_executor = os.environ.get("ORCH_DEFAULT_EXECUTOR", "").strip() or executors[0]
        # Claude Code is often installed inside an IDE extension rather than on
        # PATH; when the host exports its own path, use it instead of failing.
        claude_binary = os.environ.get("ORCH_CLAUDE_BINARY", "").strip()
        if not claude_binary:
            claude_binary = (
                os.environ.get("CLAUDE_CODE_EXECPATH", "").strip()
                if not shutil.which("claude")
                else ""
            ) or "claude"
        return cls(
            data_dir=data_dir,
            host=os.environ.get("ORCH_HOST", "127.0.0.1"),
            port=int(os.environ.get("ORCH_PORT", "8765")),
            max_workers=max(1, int(os.environ.get("ORCH_MAX_WORKERS", "2"))),
            codex_binary=os.environ.get("ORCH_CODEX_BINARY", "codex"),
            codex_model=os.environ.get("ORCH_CODEX_MODEL", ""),
            codex_model_high=os.environ.get(
                "ORCH_CODEX_MODEL_HIGH", "gpt-5.6-sol"
            ),
            codex_model_balanced=os.environ.get(
                "ORCH_CODEX_MODEL_BALANCED", "gpt-5.6-terra"
            ),
            codex_model_economy=os.environ.get(
                "ORCH_CODEX_MODEL_ECONOMY", "gpt-5.6-luna"
            ),
            claude_binary=claude_binary,
            claude_model=os.environ.get("ORCH_CLAUDE_MODEL", ""),
            claude_model_high=os.environ.get("ORCH_CLAUDE_MODEL_HIGH", "opus"),
            claude_model_balanced=os.environ.get(
                "ORCH_CLAUDE_MODEL_BALANCED", "sonnet"
            ),
            claude_model_economy=os.environ.get(
                "ORCH_CLAUDE_MODEL_ECONOMY", "haiku"
            ),
            executors=executors,
            default_executor=default_executor,
            cross_review=os.environ.get("ORCH_CROSS_REVIEW", "1").strip().lower()
            not in ("0", "false", "no"),
            quota_scheduling=os.environ.get("ORCH_QUOTA_SCHEDULING", "1")
            .strip()
            .lower()
            not in ("0", "false", "no"),
            quota_cache_seconds=max(
                5, int(os.environ.get("ORCH_QUOTA_CACHE_SECONDS", "60"))
            ),
            run_timeout_seconds=max(
                60, int(os.environ.get("ORCH_RUN_TIMEOUT_SECONDS", "3600"))
            ),
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "worktrees").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "runs").mkdir(parents=True, exist_ok=True)

    @property
    def database_path(self) -> Path:
        return self.data_dir / "orchestrator.sqlite3"

    @property
    def worktrees_dir(self) -> Path:
        return self.data_dir / "worktrees"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"
