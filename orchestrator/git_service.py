from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class GitError(RuntimeError):
    pass


SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")


def slugify(value: str, max_length: int = 36) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return (value or "task")[:max_length].rstrip("-")


class GitService:
    def __init__(self, worktrees_root: Path):
        self.worktrees_root = Path(worktrees_root).resolve()
        self.worktrees_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _run(
        args: Sequence[str],
        cwd: Optional[Path] = None,
        check: bool = True,
        timeout: int = 120,
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                list(args),
                cwd=str(cwd) if cwd else None,
                text=True,
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitError("Git command failed to start: %s" % exc) from exc
        if check and result.returncode != 0:
            message = (result.stderr or result.stdout or "git command failed").strip()
            raise GitError(message)
        return result

    def validate_repository(self, repo_path: str, base_ref: str = "HEAD") -> Dict[str, str]:
        path = Path(repo_path).expanduser().resolve()
        if not path.is_dir():
            raise GitError("Repository directory does not exist: %s" % path)
        top = self._run(
            ["git", "rev-parse", "--show-toplevel"], cwd=path
        ).stdout.strip()
        root = Path(top).resolve()
        if root != path:
            path = root
        self._validate_ref(base_ref)
        self._run(["git", "rev-parse", "--verify", base_ref], cwd=path)
        current = self._run(
            ["git", "branch", "--show-current"], cwd=path
        ).stdout.strip()
        head = self._run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()
        return {
            "repo_path": str(path),
            "current_branch": current or "(detached)",
            "head": head,
            "base_ref": base_ref,
        }

    @staticmethod
    def _validate_ref(ref: str) -> None:
        if ref == "HEAD":
            return
        if not SAFE_REF.fullmatch(ref) or ".." in ref or ref.endswith("/"):
            raise GitError("Unsafe or invalid Git ref: %s" % ref)

    def prepare_worktree(
        self,
        project_id: str,
        task_id: str,
        title: str,
        repo_path: str,
        base_ref: str,
        existing_path: Optional[str] = None,
        existing_branch: Optional[str] = None,
    ) -> Dict[str, str]:
        repo = Path(self.validate_repository(repo_path, base_ref)["repo_path"])
        if existing_path:
            path = Path(existing_path).resolve()
            if path.is_dir():
                self._assert_managed_path(path)
                self._run(["git", "status", "--porcelain"], cwd=path)
                return {"worktree_path": str(path), "branch_name": existing_branch or ""}

        project_root = (self.worktrees_root / project_id).resolve()
        path = (project_root / task_id).resolve()
        self._assert_managed_path(path)
        project_root.mkdir(parents=True, exist_ok=True)
        branch = existing_branch or "agent/%s-%s" % (task_id, slugify(title))

        if path.exists():
            if any(path.iterdir()):
                raise GitError("Managed worktree path already exists and is not empty: %s" % path)
            path.rmdir()

        branch_exists = self._run(
            ["git", "show-ref", "--verify", "--quiet", "refs/heads/%s" % branch],
            cwd=repo,
            check=False,
        ).returncode == 0
        if branch_exists:
            args: List[str] = ["git", "worktree", "add", str(path), branch]
        else:
            args = ["git", "worktree", "add", "-b", branch, str(path), base_ref]
        self._run(args, cwd=repo, timeout=300)
        return {"worktree_path": str(path), "branch_name": branch}

    def _assert_managed_path(self, path: Path) -> None:
        resolved = path.resolve()
        try:
            resolved.relative_to(self.worktrees_root)
        except ValueError as exc:
            raise GitError("Worktree path is outside the managed data directory") from exc

    @staticmethod
    def tracked_modifications(status: str) -> List[str]:
        """Paths of tracked files a run changed, ignoring untracked artefacts.

        `git status --short` marks untracked entries with `??`. Test caches and
        scratch files land there and are harmless; edits to files already under
        version control are what a read-only contract forbids.
        """
        changed: List[str] = []
        for line in (status or "").splitlines():
            if not line.strip() or line.startswith("??"):
                continue
            path = line[3:].strip()
            if path:
                changed.append(path)
        return changed

    @staticmethod
    def worktree_modifications(status: str) -> List[str]:
        """Every visible worktree path, including newly created files.

        Reviewers may leave ignored test caches behind, but any path visible in
        porcelain status would be staged by ``git add -A``.  Such a path must
        therefore stop the reviewer contract before the commit step.
        """
        changed: List[str] = []
        for line in (status or "").splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if path:
                changed.append(path)
        return changed

    def snapshot(self, worktree_path: str, max_diff_chars: int = 50000) -> Dict[str, Any]:
        path = Path(worktree_path).resolve()
        self._assert_managed_path(path)
        status = self._run(["git", "status", "--short"], cwd=path).stdout
        stat = self._run(["git", "diff", "--stat", "HEAD"], cwd=path).stdout
        diff = self._run(["git", "diff", "HEAD"], cwd=path).stdout
        truncated = len(diff) > max_diff_chars
        if truncated:
            diff = diff[:max_diff_chars] + "\n... diff truncated by orchestrator ...\n"
        return {
            "status": status,
            "stat": stat,
            "diff": diff,
            "diff_truncated": truncated,
        }

    def branch_snapshot(
        self, worktree_path: str, base_ref: str, max_diff_chars: int = 80000
    ) -> Dict[str, Any]:
        path = Path(worktree_path).resolve()
        self._assert_managed_path(path)
        self._validate_ref(base_ref)
        status = self._run(["git", "status", "--short"], cwd=path).stdout
        commits = self._run(
            ["git", "log", "--oneline", "%s..HEAD" % base_ref], cwd=path
        ).stdout
        stat = self._run(
            ["git", "diff", "--stat", "%s...HEAD" % base_ref], cwd=path
        ).stdout
        diff = self._run(
            ["git", "diff", "%s...HEAD" % base_ref], cwd=path
        ).stdout
        truncated = len(diff) > max_diff_chars
        if truncated:
            diff = diff[:max_diff_chars] + "\n... diff truncated by orchestrator ...\n"
        return {
            "status": status,
            "commits": commits,
            "stat": stat,
            "diff": diff,
            "diff_truncated": truncated,
        }

    def integrate_branches(self, worktree_path: str, branches: Sequence[str]) -> List[str]:
        path = Path(worktree_path).resolve()
        self._assert_managed_path(path)
        merged: List[str] = []
        for branch in branches:
            if not branch:
                continue
            result = self._run(
                [
                    "git",
                    "-c",
                    "user.name=Codex Orchestrator",
                    "-c",
                    "user.email=codex-orchestrator@localhost",
                    "merge",
                    "--no-edit",
                    branch,
                ],
                cwd=path,
                check=False,
                timeout=300,
            )
            if result.returncode != 0:
                self._run(["git", "merge", "--abort"], cwd=path, check=False)
                raise GitError(
                    "Could not integrate dependency branch %s: %s"
                    % (branch, (result.stderr or result.stdout).strip())
                )
            merged.append(branch)
        return merged

    def fast_forward_project(
        self, repo_path: str, base_ref: str, task_branch: str
    ) -> Dict[str, str]:
        """Advance the checked-out project branch after a human approval.

        Agent runs never call this method.  It is deliberately limited to a
        clean, already checked-out local base branch and refuses merge commits,
        detached refs, conflicts, or any attempt to overwrite operator edits.
        """

        self._validate_ref(base_ref)
        self._validate_ref(task_branch)
        repo = Path(self.validate_repository(repo_path, base_ref)["repo_path"])

        status = self._run(["git", "status", "--porcelain"], cwd=repo).stdout.strip()
        if status:
            raise GitError(
                "Project worktree has uncommitted changes; approval cannot update %s"
                % base_ref
            )

        base_symbolic = self._run(
            ["git", "rev-parse", "--symbolic-full-name", base_ref], cwd=repo
        ).stdout.strip()
        head_symbolic = self._run(
            ["git", "rev-parse", "--symbolic-full-name", "HEAD"], cwd=repo
        ).stdout.strip()
        if not base_symbolic.startswith("refs/heads/") or head_symbolic != base_symbolic:
            raise GitError(
                "Project base branch %s must be checked out before approval" % base_ref
            )

        before = self._run(["git", "rev-parse", base_ref], cwd=repo).stdout.strip()
        task_head = self._run(
            ["git", "rev-parse", "--verify", task_branch], cwd=repo
        ).stdout.strip()
        ancestor = self._run(
            ["git", "merge-base", "--is-ancestor", base_ref, task_branch],
            cwd=repo,
            check=False,
        )
        if ancestor.returncode != 0:
            raise GitError(
                "Task branch %s cannot be fast-forwarded onto %s"
                % (task_branch, base_ref)
            )

        if before != task_head:
            self._run(
                ["git", "merge", "--ff-only", task_branch],
                cwd=repo,
                timeout=300,
            )
        after = self._run(["git", "rev-parse", "HEAD"], cwd=repo).stdout.strip()
        return {
            "base_ref": base_ref,
            "branch": task_branch,
            "before": before,
            "after": after,
        }

    def commit_changes(self, worktree_path: str, task_id: str, title: str) -> Optional[str]:
        path = Path(worktree_path).resolve()
        self._assert_managed_path(path)
        status = self._run(["git", "status", "--porcelain"], cwd=path).stdout.strip()
        if not status:
            return None
        self._run(["git", "add", "-A"], cwd=path)
        message = "chore(agent): %s [%s]" % (title[:72], task_id)
        self._run(
            [
                "git",
                "-c",
                "user.name=Codex Orchestrator",
                "-c",
                "user.email=codex-orchestrator@localhost",
                "commit",
                "-m",
                message,
            ],
            cwd=path,
            timeout=300,
        )
        return self._run(["git", "rev-parse", "HEAD"], cwd=path).stdout.strip()

    def remove_worktree(self, repo_path: str, worktree_path: str) -> None:
        repo = Path(repo_path).expanduser().resolve()
        path = Path(worktree_path).resolve()
        self._assert_managed_path(path)
        self._run(["git", "worktree", "remove", str(path)], cwd=repo)
        if path.exists():
            shutil.rmtree(str(path))
