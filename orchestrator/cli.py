from __future__ import annotations

import argparse
import json
from dataclasses import replace
import sys
import threading
import webbrowser
from pathlib import Path

from .agents import build_registry
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
    agents = build_registry(
        config,
        Path(__file__).resolve().parent / "schemas" / "agent_result.schema.json",
    )
    scheduler = TaskScheduler(
        db, git, agents, config.max_workers, cross_review=config.cross_review
    )
    return db, git, agents, scheduler


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
    mcp = sub.add_parser(
        "mcp", help="Expose the running orchestrator to any MCP client over stdio"
    )
    mcp.add_argument("--url", help="Orchestrator base URL (default: configured host/port)")
    mcp.add_argument("--timeout", type=float, default=30.0)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    command = args.command or "serve"
    config = Config.from_env(project_root())
    if command == "serve":
        if getattr(args, "host", None) or getattr(args, "port", None):
            config = replace(
                config,
                host=args.host or config.host,
                port=args.port or config.port,
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
    if command == "mcp":
        # Deliberately does not call build_runtime: the MCP bridge owns no
        # database, no scheduler and no Codex process. It only talks to the
        # loopback HTTP API of a running `serve`.
        from . import mcp_server

        argv_mcp = []
        if getattr(args, "url", None):
            argv_mcp += ["--url", args.url]
        if getattr(args, "timeout", None):
            argv_mcp += ["--timeout", str(args.timeout)]
        return mcp_server.main(argv_mcp)
    if command == "doctor":
        _db, _git, agents, _scheduler = build_runtime(config)
        result = agents.preflight()
        print(
            json.dumps(
                {
                    "ok": result.ok,
                    "codex_version": result.version,
                    "auth_status": result.auth_status,
                    "problems": result.problems,
                    "default_executor": agents.default_name,
                    "executors": agents.health()["executors"],
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

