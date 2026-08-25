from __future__ import annotations

import argparse
import json
from dataclasses import replace
import subprocess
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
        description="Governance policy layer for LobeHub's local Codex workflow",
    )
    sub = result.add_subparsers(dest="command")
    sub.add_parser(
        "doctor",
        help="Check the selected LobeHub target and local Codex subscription runtime",
    )
    sub.add_parser("mcp", help="Expose stateless governance tools to LobeHub over stdio")
    sub.add_parser("lobehub-config", help="Print the JSON configuration to import in LobeHub")
    sub.add_parser(
        "login",
        help="Establish identity on ORCH_LOBEHUB_SERVER (local self-host or cloud)",
    )
    sub.add_parser("codex-login", help="Log in the local Codex CLI using ChatGPT subscription access")
    bootstrap = sub.add_parser("bootstrap", help="Create the native LobeHub project and one-round verification rubric")
    bootstrap.add_argument("--project-name", required=True)
    bootstrap.add_argument("--repo", required=True)
    bootstrap.add_argument("--identifier", default="DEV")
    goal = sub.add_parser("goal", help="Create one continuity-first root Task in a LobeHub project")
    goal.add_argument("--project", required=True, help="LobeHub project id")
    goal.add_argument("--name", required=True)
    goal.add_argument("--goal", required=True)
    goal.add_argument("--outcome", default="", help="Observable user/operator outcome")
    goal.add_argument("--accept", action="append", default=[], help="Acceptance criterion; repeatable")
    goal.add_argument("--non-goal", action="append", default=[], help="Explicit scope exclusion; repeatable")
    goal.add_argument("--prohibit", action="append", default=[], help="Prohibited behavior; repeatable")
    goal.add_argument("--assumption", action="append", default=[], help="Assumption to validate; repeatable")
    goal.add_argument("--constraint", action="append", default=[], help="Delivery constraint; repeatable")
    goal.add_argument(
        "--surface",
        action="append",
        default=[],
        choices=(
            "docs", "ui", "api", "cli", "library", "data", "migration",
            "auth", "permissions", "billing", "dependency", "infrastructure",
            "background_job", "external_integration", "observability",
        ),
        help="Changed surface; repeatable",
    )
    goal.add_argument("--security-boundary", action="append", default=[])
    goal.add_argument("--rollback", default="")
    goal.add_argument("--observe", action="append", default=[], help="Success/failure signal; repeatable")
    goal.add_argument("--performance-budget", action="append", default=[])
    goal.add_argument("--risk", choices=("low", "medium", "high", "critical"), default="medium")
    goal.add_argument("--subjective", action="store_true")
    goal.add_argument("--irreversible", action="store_true")
    goal.add_argument("--external-effects", action="store_true")
    goal.add_argument("--security-sensitive", action="store_true")
    execute_topic = sub.add_parser(
        "execute-topic",
        help="Run a frozen LobeHub Task Topic through the safe local Codex harness",
    )
    execute_topic.add_argument("--task", required=True, help="LobeHub Task id or identifier")
    execute_topic.add_argument("--topic", required=True, help="Existing Topic belonging to the Task")
    execute_topic.add_argument("--repo", required=True, help="Repository used as Codex cwd")
    execute_topic.add_argument(
        "--sandbox",
        choices=("read-only", "workspace-write"),
        required=True,
    )
    execute_topic.add_argument("--model", default="", help="Optional Codex model override")
    execute_topic.add_argument("--operation", default="", help="Optional operation id")
    execute_topic.add_argument("--resume", default="", help="Optional native Codex session id")
    execute_topic.add_argument("--timeout", type=int, default=3600)
    governed = sub.add_parser(
        "run-governed-task",
        help="Advance or recover the nine-stage LobeHub/Codex delivery loop",
    )
    governed_source = governed.add_mutually_exclusive_group(required=True)
    governed_source.add_argument("--spec", help="New governed-task JSON specification")
    governed_source.add_argument("--resume", help="Run id or journal path to recover")
    governed.add_argument("--run-id", default="", help="Optional stable id for a new run")
    governed.add_argument(
        "--runs-dir",
        default="",
        help="Recovery journal directory (default: .data/governed-runs)",
    )
    governed.add_argument(
        "--stop-after",
        default="",
        help="Stop after a named safe stage to rehearse recovery",
    )
    governed.add_argument("--timeout", type=int, default=3600)
    governed.add_argument(
        "--approve-material-execution",
        action="store_true",
        help="Record this operator's explicit approval at a paused high-risk execution gate",
    )
    governed.add_argument(
        "--decision-note",
        default="",
        help="Optional non-secret note attached to an explicit operator decision",
    )
    governed_status = sub.add_parser(
        "governed-task-status",
        help="Read one governed run journal without advancing it",
    )
    governed_status.add_argument("run", help="Run id or journal path")
    governed_status.add_argument("--runs-dir", default="")

    # The former all-in-one board remains reachable for data migration and
    # regression comparison, but it is no longer the product entry point.
    serve = sub.add_parser("legacy-serve", help="Start the retired local v1 board")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--open", action="store_true", help="Open the board in the default browser")
    legacy_mcp = sub.add_parser(
        "legacy-mcp", help="Expose the retired v1 board bridge over stdio"
    )
    legacy_mcp.add_argument("--url", help="Legacy board base URL")
    legacy_mcp.add_argument("--timeout", type=float, default=30.0)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    command = args.command or "doctor"
    config = Config.from_env(project_root())
    if command == "legacy-serve":
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
    if command == "legacy-mcp":
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
    if command == "mcp":
        from .governance_mcp import main as governance_main

        return governance_main()
    if command == "doctor":
        from .lobehub import doctor

        result = doctor(project_root())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1
    if command == "lobehub-config":
        from .lobehub import integration_config

        print(json.dumps(integration_config(project_root()), ensure_ascii=False, indent=2))
        return 0
    if command == "login":
        from .lobehub import login

        return login()
    if command == "codex-login":
        from .lobehub import codex_binary

        return subprocess.call([codex_binary(), "login"])
    if command == "bootstrap":
        from .lobehub import bootstrap

        result = bootstrap(
            args.project_name,
            Path(args.repo),
            identifier=args.identifier,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == "goal":
        from .lobehub import create_goal_task

        result = create_goal_task(
            project_id=args.project,
            name=args.name,
            goal=args.goal,
            acceptance_criteria=args.accept,
            risk=args.risk,
            agent_id="",
            subjective=args.subjective,
            irreversible=args.irreversible,
            external_effects=args.external_effects,
            security_sensitive=args.security_sensitive,
            user_outcome=args.outcome,
            non_goals=args.non_goal,
            prohibited_behaviors=args.prohibit,
            assumptions=args.assumption,
            constraints=args.constraint,
            change_surfaces=args.surface,
            security_boundaries=args.security_boundary,
            rollback_plan=args.rollback,
            observability_signals=args.observe,
            performance_budgets=args.performance_budget,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == "execute-topic":
        from .lobehub import run_task_topic_with_codex

        result = run_task_topic_with_codex(
            task_id=args.task,
            topic_id=args.topic,
            cwd=Path(args.repo),
            sandbox=args.sandbox,
            model=args.model,
            operation_id=args.operation,
            session_id=args.resume,
            timeout=args.timeout,
        )
        # Events are offered to LobeHub ingest and final text is persisted via
        # its released message command. Keep terminal output small.
        result.pop("events", None)
        result.pop("command", None)
        result.pop("final_text", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command in {"run-governed-task", "governed-task-status"}:
        from .governed_task import (
            GovernedTaskLoop,
            RunJournal,
            load_spec,
            resolve_run_path,
        )

        runs_dir = Path(getattr(args, "runs_dir", "") or (project_root() / ".data" / "governed-runs"))
        if command == "governed-task-status":
            journal = RunJournal.load(resolve_run_path(args.run, runs_dir))
            # Status is intentionally read-only and does not require LobeHub.
            current = next(
                (
                    name
                    for name, stage in journal.data["stages"].items()
                    if stage["status"] not in {"completed", "skipped"}
                ),
                list(journal.data["stages"])[-1],
            )
            print(
                json.dumps(
                    {
                        "run_id": journal.data["run_id"],
                        "status": journal.data["status"],
                        "current_stage": current,
                        "resources": journal.data.get("resources", {}),
                        "pause": journal.data.get("pause"),
                        "journal": str(journal.path),
                        "stages": {
                            name: stage["status"]
                            for name, stage in journal.data["stages"].items()
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.spec:
            if args.approve_material_execution:
                raise ValueError(
                    "--approve-material-execution is only valid while resuming a paused run"
                )
            journal = RunJournal.create(
                runs_dir,
                load_spec(Path(args.spec)),
                run_id=args.run_id,
            )
        else:
            journal = RunJournal.load(resolve_run_path(args.resume, runs_dir))
            if args.approve_material_execution:
                journal.acquire()
                try:
                    decisions = journal.data["spec"].setdefault("decisions", {})
                    decisions["material_execution_approved"] = True
                    journal.append_event(
                        "human_decision",
                        "05_owner_delivered",
                        "waiting",
                        "approved",
                        args.decision_note.strip()
                        or "操作人明确批准进入材料执行；这不是 Agent 模拟审批。",
                    )
                finally:
                    journal.release()
        summary = GovernedTaskLoop(journal, timeout=args.timeout).run(
            stop_after=args.stop_after
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        if summary["status"] == "interrupted":
            return 1
        if summary["status"] in {
            "needs_clarification", "needs_configuration", "needs_human_decision",
            "quota_deferred",
        }:
            return 3
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
