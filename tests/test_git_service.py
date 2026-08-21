from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.git_service import GitError, GitService
from tests.helpers import make_git_repo


class GitServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = make_git_repo(self.root)
        self.git = GitService(self.root / "worktrees")

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_branch_worktree_and_chains_from_it(self):
        first = self.git.prepare_worktree(
            "project", "task_one", "First task", str(self.repo), "main"
        )
        first_path = Path(first["worktree_path"])
        (first_path / "feature.txt").write_text("implemented\n", encoding="utf-8")
        commit = self.git.commit_changes(str(first_path), "task_one", "First task")
        self.assertTrue(commit)

        second = self.git.prepare_worktree(
            "project", "task_two", "Second task", str(self.repo), first["branch_name"]
        )
        self.assertEqual(
            (Path(second["worktree_path"]) / "feature.txt").read_text(encoding="utf-8"),
            "implemented\n",
        )
        snapshot = self.git.branch_snapshot(second["worktree_path"], "main")
        self.assertIn("feature.txt", snapshot["stat"])

    def test_rejects_invalid_ref(self):
        with self.assertRaises(GitError):
            self.git.validate_repository(str(self.repo), "--upload-pack=bad")

    def test_tracked_modifications_ignores_untracked_files(self):
        status = " M orchestrator/web.py\n?? .pytest_cache/\nA  new_file.py\n?? scratch.log\n"
        self.assertEqual(
            GitService.tracked_modifications(status),
            ["orchestrator/web.py", "new_file.py"],
        )
        self.assertEqual(GitService.tracked_modifications(""), [])
        self.assertEqual(GitService.tracked_modifications("?? only_untracked\n"), [])


if __name__ == "__main__":
    unittest.main()

