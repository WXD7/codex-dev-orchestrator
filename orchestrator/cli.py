from __future__ import annotations

import argparse
import json
import sys
import threading
import webbrowser
from pathlib import Path

from .codex_agent import CodexAgent
from .config import Config
from .database import Database
from .git_service import GitService
from .scheduler import TaskScheduler
from .web import Application, create_server


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def build_runtime(config: Config):
    config.ensure_directories()
    db = Database(config.database_path)
    db.initialize()
    git = GitService(config.worktrees_dir)
    agent = CodexAgent(
        binary=config.codex_binary,
        schema_path=Path(__file__).resolve().parent / "schemas" / "agent_result.schema.json",
        runs_dir=config.runs_dir,
        model=config.codex_model,
        timeout_seconds=config.run_timeout_seconds,
    )
    scheduler = TaskScheduler(db, git, agent, config.max_workers)
    return db, git, agent, scheduler


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="codex-orchestrator",
        description="Local Codex-powered development workflow orchestrator",
    )
    sub = result.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Start the local board and worker service")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--open", action="store_true", help="Open the board in the default browser")
    sub.add_parser("doctor", help="Check Codex subscription authentication and local prerequisites")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    command = args.command or "serve"
    config = Config.from_env(project_root())
    if command == "serve":
        if getattr(args, "host", None) or getattr(args, "port", None):
            config = Config(
                data_dir=config.data_dir,
                host=args.host or config.host,
                port=args.port or config.port,
                max_workers=config.max_workers,
                codex_binary=config.codex_binary,
                codex_model=config.codex_model,
                run_timeout_seconds=config.run_timeout_seconds,
            )
        db, git, agent, scheduler = build_runtime(config)
        scheduler.start()
        app = Application(
            db,
            git,
            scheduler,
            Path(__file__).resolve().parent / "static",
        )
        server = create_server(config.host, config.port, app)
        url = "http://%s:%d" % (config.host, config.port)
        print("Codex Dev Orchestrator: %s" % url)
        health = scheduler.health()
        if not health["ready"]:
            print("Codex worker unavailable: %s" % "; ".join(health["problems"]))
        if getattr(args, "open", False):
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever(poll_interval=0.5)
        except KeyboardInterrupt:
            print("\nStopping...")
        finally:
            server.shutdown()
            scheduler.stop()
            server.server_close()
        return 0
    if command == "doctor":
        _db, _git, agent, _scheduler = build_runtime(config)
        result = agent.preflight()
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "codex_version": result.version,
                    "auth_status": result.auth_status,
                    "problems": result.problems,
                    "data_dir": str(config.data_dir),
                    "api_keys_forwarded": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if result.ok else 1
    return 2


if __name__ == "__main__":
    sys.exit(main())

