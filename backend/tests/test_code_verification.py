import asyncio
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _git(args, cwd):
    subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _init_repo(path):
    _git(["init"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "Test"], path)
    _git(["config", "commit.gpgsign", "false"], path)


class SummarizeRepoChangesTests(unittest.TestCase):
    def test_structured_summary_from_temp_git_repo(self):
        from code_verification import summarize_repo_changes

        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            # Committed base file so we have a tracked file to modify.
            with open(os.path.join(repo, "tracked.txt"), "w") as fh:
                fh.write("base\n")
            _git(["add", "tracked.txt"], repo)
            _git(["commit", "-m", "base"], repo)

            # Unstaged change to a tracked file.
            with open(os.path.join(repo, "tracked.txt"), "w") as fh:
                fh.write("base\nmore\n")
            # Staged new file.
            with open(os.path.join(repo, "staged.txt"), "w") as fh:
                fh.write("staged\n")
            _git(["add", "staged.txt"], repo)
            # Untracked file.
            with open(os.path.join(repo, "untracked.txt"), "w") as fh:
                fh.write("loose\n")

            summary = asyncio.run(summarize_repo_changes(repo))

            self.assertTrue(summary["is_git_repo"])
            self.assertIn("tracked.txt", summary["changed_files"])
            self.assertIn("staged.txt", summary["changed_files"])
            self.assertIn("untracked.txt", summary["untracked"])
            self.assertGreaterEqual(summary["insertions"], 1)

    def test_before_after_isolates_turn_changes(self):
        from code_verification import git_status_snapshot, summarize_repo_changes

        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            with open(os.path.join(repo, "pre.txt"), "w") as fh:
                fh.write("pre\n")

            before = asyncio.run(git_status_snapshot(repo))
            self.assertIn("pre.txt", before)

            with open(os.path.join(repo, "new.txt"), "w") as fh:
                fh.write("new\n")

            summary = asyncio.run(summarize_repo_changes(repo, before=before))
            self.assertEqual(["new.txt"], summary["turn_changed_files"])

    def test_non_git_cwd_handled_gracefully(self):
        from code_verification import summarize_repo_changes

        with tempfile.TemporaryDirectory() as plain:
            summary = asyncio.run(summarize_repo_changes(plain))
            self.assertFalse(summary["is_git_repo"])
            self.assertEqual([], summary["changed_files"])

    def test_missing_cwd_handled_gracefully(self):
        from code_verification import summarize_repo_changes

        summary = asyncio.run(summarize_repo_changes("/no/such/path/xyz"))
        self.assertFalse(summary["is_git_repo"])


class OutsideChangeDetectionTests(unittest.TestCase):
    def test_path_outside_cwd_is_flagged(self):
        from code_verification import _outside_changes

        cwd = "/home/user/project"
        outside = _outside_changes(["src/a.py", "../other/b.py"], cwd)
        self.assertIn("../other/b.py", outside)
        self.assertNotIn("src/a.py", outside)

    def test_absolute_path_outside_cwd_is_flagged(self):
        from code_verification import _outside_changes

        cwd = "/home/user/project"
        outside = _outside_changes(["/etc/passwd"], cwd)
        self.assertEqual(["/etc/passwd"], outside)

    def test_verify_code_run_sets_unexpected_changes_flag(self):
        from code_verification import verify_code_run

        with tempfile.TemporaryDirectory() as parent:
            repo = os.path.join(parent, "repo")
            os.makedirs(repo)
            _init_repo(repo)
            with open(os.path.join(repo, "base.txt"), "w") as fh:
                fh.write("base\n")
            _git(["add", "."], repo)
            _git(["commit", "-m", "base"], repo)

            # A change escaping cwd via a relative path inside the repo tree:
            # simulate by checking the helper output shape with a normal change.
            with open(os.path.join(repo, "x.txt"), "w") as fh:
                fh.write("x\n")

            artifact = asyncio.run(verify_code_run(repo))
            self.assertTrue(artifact["is_git_repo"])
            self.assertFalse(artifact["unexpected_changes"])
            self.assertIn("x.txt", artifact["untracked"])


class VerificationCommandTests(unittest.TestCase):
    def test_skipped_with_reason_when_flag_off(self):
        from code_verification import run_verification

        with tempfile.TemporaryDirectory() as repo:
            # pyproject makes pytest a "known" command, but auto-run is off.
            with open(os.path.join(repo, "pyproject.toml"), "w") as fh:
                fh.write("[project]\nname='x'\n")
            result = asyncio.run(
                run_verification(repo, ["x.txt"], run_tests=False)
            )
            self.assertFalse(result["ran"])
            self.assertEqual("pytest -q", result["command"])
            self.assertIn("disabled", result["reason"])

    def test_skipped_with_reason_when_no_known_command(self):
        from code_verification import run_verification

        with tempfile.TemporaryDirectory() as repo:
            result = asyncio.run(
                run_verification(repo, ["x.txt"], run_tests=True)
            )
            self.assertFalse(result["ran"])
            self.assertIn("no known verification command", result["reason"])

    def test_skipped_when_no_files_changed(self):
        from code_verification import run_verification

        with tempfile.TemporaryDirectory() as repo:
            result = asyncio.run(run_verification(repo, [], run_tests=True))
            self.assertFalse(result["ran"])
            self.assertIn("no files changed", result["reason"])

    def test_run_path_with_trivial_passing_command(self):

        # npm/pnpm/pytest aren't guaranteed installed; exercise the run path by
        # monkeypatching the detected command to a trivial always-passing one.
        import code_verification as cv

        orig = cv._detect_verification_command
        cv._detect_verification_command = lambda cwd: (
            [sys.executable, "-c", "pass"],
            "noop",
        )
        try:
            with tempfile.TemporaryDirectory() as repo:
                result = asyncio.run(
                    cv.run_verification(repo, ["x.txt"], run_tests=True)
                )
        finally:
            cv._detect_verification_command = orig

        self.assertTrue(result["ran"])
        self.assertTrue(result["passed"])
        self.assertEqual(0, result["returncode"])

    def test_detects_npm_test_script(self):
        from code_verification import _detect_verification_command

        with tempfile.TemporaryDirectory() as repo:
            with open(os.path.join(repo, "package.json"), "w") as fh:
                fh.write('{"scripts": {"test": "jest"}}')
            cmd, label = _detect_verification_command(repo)
            self.assertEqual(["npm", "test"], cmd)
            self.assertEqual("npm test", label)


class VerifyCodeRunArtifactTests(unittest.TestCase):
    def test_artifact_includes_changed_files_metadata(self):
        from code_verification import git_status_snapshot, verify_code_run

        with tempfile.TemporaryDirectory() as repo:
            _init_repo(repo)
            with open(os.path.join(repo, "base.txt"), "w") as fh:
                fh.write("base\n")
            _git(["add", "."], repo)
            _git(["commit", "-m", "base"], repo)

            before = asyncio.run(git_status_snapshot(repo))
            with open(os.path.join(repo, "feature.txt"), "w") as fh:
                fh.write("feature\n")

            artifact = asyncio.run(verify_code_run(repo, before=before))
            self.assertTrue(artifact["is_git_repo"])
            self.assertEqual(["feature.txt"], artifact["turn_changed_files"])
            self.assertIn("verification", artifact)
            self.assertFalse(artifact["verification"]["ran"])

    def test_artifact_for_non_git_cwd(self):
        from code_verification import verify_code_run

        with tempfile.TemporaryDirectory() as plain:
            artifact = asyncio.run(verify_code_run(plain))
            self.assertFalse(artifact["is_git_repo"])
            self.assertFalse(artifact["unexpected_changes"])
            self.assertIn("verification", artifact)


if __name__ == "__main__":
    unittest.main()
