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
    governance = sub.add_parser(
        "governance",
        help="Compile contracts, route verification, and adjudicate evidence without running an agent",
    )
    governance_sub = governance.add_subparsers(dest="governance_command", required=True)
    for name, help_text in (
        ("research", "Compile four-channel technology research and independent quality review"),
        ("intent", "Compile a human-facing intent confirmation brief"),
        ("inspect-intent", "Compile a fresh read-only intent inspection result"),
        ("compile", "Compile a raw work contract"),
        ("resolve", "Compile a proposed hash-bound resolution for contract questions"),
        ("cases", "Compile a human-confirmed hidden Bad Case registry"),
        ("calibrate", "Calibrate an Inspector against labelled Good/Bad Cases"),
        ("route", "Build a risk-selected verification plan from a compiled contract"),
        ("contexts", "Build isolated inspector context packets"),
        ("handoff", "Build a side-effect-free Kandev/Codex execution manifest"),
        ("preflight", "Build a read-only V2 environment capsule for a repository"),
        ("adjudicate", "Adjudicate deterministic results and semantic findings"),
    ):
        item = governance_sub.add_parser(name, help=help_text)
        item.add_argument("--input", default="-", help="JSON file, or - for stdin")
        item.add_argument("--output", default="-", help="JSON file, or - for stdout")
    governance_sub.add_parser(
        "blueprint", help="Show ownership boundaries for the mature external components"
    )
    init = governance_sub.add_parser(
        "init", help="Create a versioned .ai-delivery policy bundle in an existing Git repository"
    )
    init.add_argument("--target", required=True)
    init.add_argument("--input", default="-", help="Raw contract JSON file, or - for stdin")
    init.add_argument("--output", default="-", help="Result JSON file, or - for stdout")
    takeover = governance_sub.add_parser(
        "takeover",
        help="Verify one project-isolated continuity packet for a fresh conversation",
    )
    takeover.add_argument(
        "--target",
        default=".",
        help="Exact Git repository to bind before reading project-specific context",
    )
    takeover.add_argument("--output", default="-", help="JSON file, or - for stdout")
    sub.add_parser(
        "governance-mcp",
        help="Expose pure delivery-governance tools over stdio without a database or HTTP service",
    )
    return result


def _read_json_input(path: str) -> dict:
    raw = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("input JSON must be an object")
    return value


def _write_json_output(path: str, value) -> None:
    rendered = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if path == "-":
        sys.stdout.write(rendered)
        return
    output = Path(path)
    if output.exists():
        raise FileExistsError("Refusing to overwrite existing output: %s" % output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8")


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
    if command == "governance-mcp":
        from . import governance_mcp

        return governance_mcp.main([])
    if command == "governance":
        from .delivery_bundle import scaffold_project
        from .governance import GovernanceEngine, integration_blueprint
        from .governance_learning import (
            compile_bad_case_registry,
            compile_inspector_calibration,
        )
        from .governance_runtime import build_environment_capsule

        engine = GovernanceEngine()
        operation = args.governance_command
        if operation == "blueprint":
            _write_json_output("-", integration_blueprint())
            return 0
        if operation == "takeover":
            from .continuity import build_takeover_packet

            result = build_takeover_packet(Path(args.target))
            _write_json_output(args.output, result)
            return 0 if result.get("ready_for_takeover") else 1
        source = _read_json_input(args.input)
        if operation == "research":
            result = engine.compile_technology_research(source)
        elif operation == "intent":
            result = engine.compile_intent_brief(source)
        elif operation == "inspect-intent":
            result = engine.compile_intent_inspection(source)
        elif operation == "compile":
            result = engine.compile_contract(source)
        elif operation == "resolve":
            result = engine.propose_contract_resolution(
                source.get("contract") or {},
                source.get("answers") or [],
                source.get("field_updates") or {},
            )
        elif operation == "cases":
            result = compile_bad_case_registry(source)
        elif operation == "calibrate":
            result = compile_inspector_calibration(source)
        elif operation == "route":
            if isinstance(source.get("contract"), dict):
                result = engine.route(
                    source.get("contract") or {}, source.get("bad_case_registry")
                )
            else:
                result = engine.route(source)
        elif operation == "contexts":
            result = {
                "contexts": engine.context_packets(
                    source.get("contract") or {}, source.get("plan") or {}
                )
            }
        elif operation == "handoff":
            result = engine.delivery_handoff(
                source.get("contract") or {}, source.get("plan") or {}
            )
        elif operation == "preflight":
            contract = source.get("contract") or {}
            engine._validate_contract(contract)
            result = build_environment_capsule(Path(str(source.get("repo") or "")), contract)
        elif operation == "adjudicate":
            result = engine.adjudicate(source)
        elif operation == "init":
            result = scaffold_project(Path(args.target), source)
        else:
            return 2
        _write_json_output(args.output, result)
        return 0
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
