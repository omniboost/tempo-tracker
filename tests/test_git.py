import os
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from tempo_tracker.git import BranchWatcher, CommitLog, describe, find_repos, issue_key


def git(repo: Path, *args: str, date: str | None = None, email: str = "me@example.com") -> None:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Me", "GIT_AUTHOR_EMAIL": email, "GIT_COMMITTER_NAME": "Me", "GIT_COMMITTER_EMAIL": email}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "me@example.com")
    git(path, "commit", "-q", "--allow-empty", "-m", "Initial", date="2026-10-01T09:00:00")
    return path


class IssueKeyTest(unittest.TestCase):
    def test_finds_the_key_in_common_branch_names(self):
        cases = {
            "feature/IN-42-split-bills": "IN-42", "bugfix/CSI-1374": "CSI-1374", "IN-2858": "IN-2858",
            "internal/IN-4174": "IN-4174", "hotfix-123": None, "release-2": None, "main": None, "feature/in-12-x": None,
        }
        for branch, key in cases.items():
            with self.subTest(branch=branch):
                self.assertEqual(key, issue_key(branch))


class DescribeTest(unittest.TestCase):
    def test_strips_prefixes_and_joins(self):
        subjects = ["feat(IN-42): add tips", "IN-42: answer retries", "[IN-42] void a check", "test(IN-42): add tips"]
        self.assertEqual("Add tips; Answer retries; Void a check", describe(subjects))

    def test_is_capped_for_tempo(self):
        description = describe([f"feat(IN-1): change number {n} with some more words" for n in range(20)])
        self.assertLessEqual(len(description), 255)
        self.assertTrue(description.endswith("…"))


class GitRepoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_finds_repos_but_not_inside_them_or_in_vendor_folders(self):
        app = make_repo(self.root / "work" / "laravel" / "app")
        make_repo(self.root / "work" / "laravel" / "app" / "packages" / "inner")
        make_repo(self.root / "site" / "node_modules" / "dep")
        self.assertEqual([app], find_repos([str(self.root)]))

    def test_reports_a_switch_to_a_ticket_branch(self):
        repo = make_repo(self.root / "app")
        watcher = BranchWatcher()
        watcher.set_repos([repo])
        self.assertIsNone(watcher.poll())

        git(repo, "checkout", "-q", "-b", "feature/IN-42-thing")
        switch = watcher.poll()
        self.assertEqual(("feature/IN-42-thing", "IN-42"), (switch.branch, switch.key))
        self.assertIsNone(watcher.poll())

        git(repo, "checkout", "-q", "main")
        self.assertIsNone(watcher.poll())

    def test_a_switch_during_a_restart_is_not_lost(self):
        repo = make_repo(self.root / "app")
        before = BranchWatcher()
        before.set_repos([repo])
        saved = before.snapshot()

        git(repo, "checkout", "-q", "-b", "feature/IN-42-thing")  # terwijl de app herstart
        after = BranchWatcher(saved)
        after.set_repos([repo])
        self.assertEqual("IN-42", after.poll().key)

    def test_detached_head_and_returning_to_the_same_branch_is_not_a_switch(self):
        repo = make_repo(self.root / "app")
        git(repo, "checkout", "-q", "-b", "feature/IN-42-thing")
        watcher = BranchWatcher()
        watcher.set_repos([repo])
        git(repo, "checkout", "-q", "--detach")
        self.assertIsNone(watcher.poll())
        git(repo, "checkout", "-q", "feature/IN-42-thing")
        self.assertIsNone(watcher.poll())

    def test_commit_messages_of_the_day_per_key_only_mine(self):
        repo = make_repo(self.root / "app")
        git(repo, "commit", "-q", "--allow-empty", "-m", "feat(IN-1): yesterday", date="2026-10-19T15:00:00")
        git(repo, "commit", "-q", "--allow-empty", "-m", "feat(IN-1): first", date="2026-10-20T10:00:00")
        git(repo, "commit", "-q", "--allow-empty", "-m", "feat(IN-1): a colleague", date="2026-10-20T11:00:00", email="other@example.com")
        git(repo, "checkout", "-q", "-b", "other")
        git(repo, "commit", "-q", "--allow-empty", "-m", "IN-3: on another branch", date="2026-10-20T12:00:00")
        git(repo, "checkout", "-q", "main")
        git(repo, "commit", "-q", "--allow-empty", "-m", "fix(IN-1): second, also IN-2", date="2026-10-20T15:00:00")

        by_key = CommitLog().messages_by_key([repo], "2026-10-20")

        self.assertEqual(
            {"IN-1": ["feat(IN-1): first", "fix(IN-1): second, also IN-2"], "IN-2": ["fix(IN-1): second, also IN-2"], "IN-3": ["IN-3: on another branch"]},
            by_key,
        )

    def test_rebased_old_commits_do_not_count_for_today(self):
        repo = make_repo(self.root / "app")
        git(repo, "commit", "-q", "--allow-empty", "-m", "feat(IN-1): old work", date="2026-10-15T10:00:00")
        git(repo, "commit", "-q", "--amend", "--allow-empty", "--no-edit")  # nieuwe commitdatum, oude auteursdatum
        self.assertEqual({}, CommitLog().messages_by_key([repo], datetime.now().strftime("%Y-%m-%d")))


if __name__ == "__main__":
    unittest.main()
