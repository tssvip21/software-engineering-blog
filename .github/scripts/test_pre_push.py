"""Pre-push regression checks use only disposable repositories and synthetic data."""
from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pre_push as gate


class PushInputTests(unittest.TestCase):
    def test_empty_and_well_formed_deletion_are_allowed(self):
        self.assertEqual(gate.parse_updates(b""), [])
        line = f"(delete) {gate.ZERO} refs/heads/old {'a' * 40}\n".encode()
        self.assertEqual(gate.parse_updates(line)[0].local_sha, gate.ZERO)

    def test_malformed_input_fails_closed_without_echoing_input(self):
        marker = "synthetic-private-input"
        cases = [
            b"\n", b"\xff", b"a b c\n", b"a b c d e\n",
            f"{marker} short refs/heads/main {gate.ZERO}\n".encode(),
            f"HEAD {'a' * 39} refs/heads/main {gate.ZERO}\n".encode(),
            f"HEAD {'A' * 40} refs/heads/main {gate.ZERO}\n".encode(),
            f"HEAD {gate.ZERO} refs/heads/main {gate.ZERO}\n".encode(),
            f"(delete) {'a' * 40} refs/heads/main {gate.ZERO}\n".encode(),
            f"HEAD {'a' * 40} main {gate.ZERO}\n".encode(),
            f"HEAD {'a' * 40} refs/heads/main {gate.ZERO}\n\n".encode(),
            f"HE\tAD {'a' * 40} refs/heads/main {gate.ZERO}\n".encode(),
            f"HEAD {'a' * 40} refs/heads/main {gate.ZERO}\r\n".encode(),
            f"HEAD {'a' * 40} refs/heads/main {gate.ZERO}\n".encode() * 2,
            b"x" * (gate.MAX_INPUT_BYTES + 1),
        ]
        for data in cases:
            with self.subTest(size=len(data)):
                with self.assertRaises(gate.GateError) as failure:
                    gate.parse_updates(data)
                self.assertNotIn(marker, str(failure.exception))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix="petnote-hook-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "repository"
        self.root.mkdir()
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        env.update({
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_AUTHOR_NAME": "Test Runner", "GIT_COMMITTER_NAME": "Test Runner",
            "GIT_AUTHOR_EMAIL": "checks@example.invalid", "GIT_COMMITTER_EMAIL": "checks@example.invalid",
        })
        self.environment = patch.dict(os.environ, env, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.git("init", "-b", "main")
        for name, content in {
            "source.py": "value = 1\n",
            ".gitignore": "ignored.txt\npetnote/tmp/\n",
            ".githooks/pre-push": "#!/bin/sh\nexit 0\n",
            ".github/scripts/check_privacy.py": "# synthetic fixture\n",
            "petnote/scripts/check.py": "# synthetic fixture\n",
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-m", "Initial synthetic fixture")
        self.head = self.git("rev-parse", "HEAD").strip().decode()
        self.git("config", "petnote.checkPython", sys.executable)
        self.git("config", "petnote.gitleaks", sys.executable)

    def git(self, *args, root=None):
        return subprocess.check_output(
            ["git", *args], cwd=root or self.root, stderr=subprocess.PIPE,
            env=gate.command_env(), timeout=30,
        )

    def updates(self, sha=None, remote="refs/heads/main"):
        return [gate.PushRef("refs/heads/main", sha or self.head, remote, gate.ZERO)]

    @contextlib.contextmanager
    def checks(self, side_effect=None):
        with patch.object(gate, "verify_tools"), patch.object(gate, "run_check", side_effect=side_effect) as check:
            with contextlib.redirect_stdout(io.StringIO()):
                yield check

    def test_clean_head_runs_both_privacy_modes_scanner_and_application_checks(self):
        with self.checks() as check:
            gate.check_head(self.root, self.updates())
        self.assertEqual(check.call_count, 4)
        commands = [[str(part) for part in call.args[2]] for call in check.call_args_list]
        self.assertEqual(commands[0][-2:], ["--history", self.head])
        self.assertEqual(commands[1][-1], "--working-tree")
        for argument in [
            f"--log-opts={self.head}", "--redact=100", "--max-decode-depth=5",
            "--max-archive-depth=2", "--ignore-gitleaks-allow", "--config", "--gitleaks-ignore-path",
        ]:
            self.assertIn(argument, commands[2])
        self.assertEqual(Path(commands[3][1]), self.root / "petnote/scripts/check.py")
        self.assertNotIn("--browser", commands[3])

    def test_scanner_uses_default_rules_and_empty_ignore_file(self):
        def inspect(root, label, command, timeout):
            if label == "Gitleaks history check":
                self.assertEqual(Path(command[command.index("--config") + 1]).read_text(),
                                 "[extend]\nuseDefault = true\n")
                self.assertEqual(Path(command[command.index("--gitleaks-ignore-path") + 1]).read_text(), "")
        with self.checks(inspect):
            gate.check_head(self.root)

    def test_staged_unstaged_and_nonignored_untracked_changes_are_blocked(self):
        for kind in ("unstaged", "staged", "untracked"):
            with self.subTest(kind=kind):
                path = self.root / ("extra.py" if kind == "untracked" else "source.py")
                path.write_text("value = 2\n", encoding="utf-8")
                if kind == "staged":
                    self.git("add", "source.py")
                with self.checks() as check, self.assertRaisesRegex(gate.GateError, "staged"):
                    gate.check_head(self.root)
                check.assert_not_called()
                self.git("reset", "--hard", self.head)
                if kind == "untracked":
                    path.unlink()

    def test_ignored_artifacts_do_not_block_a_clean_commit(self):
        (self.root / "ignored.txt").write_text("local artifact", encoding="utf-8")
        with self.checks():
            gate.check_head(self.root)

    def test_hidden_index_changes_are_blocked(self):
        self.git("update-index", "--assume-unchanged", "source.py")
        (self.root / "source.py").write_text("value = 9\n", encoding="utf-8")
        with self.checks(), self.assertRaises(gate.GateError):
            gate.check_head(self.root)

    def test_other_commit_and_annotated_tag_are_blocked(self):
        self.git("tag", "-a", "synthetic-tag", "-m", "Synthetic tag")
        tag_sha = self.git("rev-parse", "synthetic-tag").strip().decode()
        for sha in ("b" * 40, tag_sha):
            with self.subTest(sha=sha), self.checks() as check:
                with self.assertRaisesRegex(gate.GateError, "HEAD"):
                    gate.check_head(self.root, self.updates(sha))
                check.assert_not_called()

    def test_invalid_destination_ref_is_blocked(self):
        with self.checks(), self.assertRaises(gate.GateError):
            gate.check_head(self.root, self.updates(remote="refs/heads/invalid..name"))

    def test_missing_tool_configuration_is_blocked(self):
        self.git("config", "--unset", "petnote.gitleaks")
        with self.checks() as check, self.assertRaisesRegex(gate.GateError, "not configured"):
            gate.check_head(self.root)
        check.assert_not_called()

    def test_any_check_failure_stops_remaining_checks(self):
        with self.checks(gate.GateError("Synthetic check failed.")) as check:
            with self.assertRaisesRegex(gate.GateError, "Synthetic check"):
                gate.check_head(self.root)
        self.assertEqual(check.call_count, 1)

    def test_source_change_during_checks_blocks_upload(self):
        def change(*args):
            (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        with self.checks(change) as check, self.assertRaises(gate.GateError):
            gate.check_head(self.root)
        self.assertEqual(check.call_count, 1)

    def test_restored_content_with_changed_metadata_is_blocked(self):
        def touch(*args):
            path = self.root / "source.py"
            before = path.stat()
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        with self.checks(touch) as check, self.assertRaisesRegex(gate.GateError, "changed during"):
            gate.check_head(self.root)
        self.assertEqual(check.call_count, 1)

    def test_head_change_during_checks_blocks_upload(self):
        def commit(*args):
            self.git("commit", "--allow-empty", "-m", "Concurrent synthetic change")
        with self.checks(commit) as check, self.assertRaisesRegex(gate.GateError, "changed during"):
            gate.check_head(self.root)
        self.assertEqual(check.call_count, 1)

    def test_check_mode_does_not_read_stdin_or_upload(self):
        with patch.object(gate, "repository_root", return_value=self.root), patch.object(gate, "check_head") as check:
            with patch.object(gate, "sys") as fake_sys:
                self.assertEqual(gate.main(["--check"]), 0)
                fake_sys.stdin.buffer.read.assert_not_called()
        check.assert_called_once_with(self.root)

    def test_deletion_only_and_no_updates_need_no_tools_or_clean_tree(self):
        self.git("config", "--unset", "petnote.gitleaks")
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        for data in (b"", f"(delete) {gate.ZERO} refs/heads/old {self.head}\n".encode()):
            with self.subTest(data=bool(data)), patch.object(gate, "repository_root", return_value=self.root):
                with patch.object(gate.sys, "stdin", io.TextIOWrapper(io.BytesIO(data))):
                    with patch.object(gate, "check_head") as check, contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(gate.main([]), 0)
                        check.assert_not_called()

    def test_mixed_deletion_and_head_update_still_runs_checks(self):
        updates = self.updates() + [gate.PushRef("(delete)", gate.ZERO, "refs/heads/old", self.head)]
        with self.checks() as check:
            gate.check_head(self.root, updates)
        self.assertEqual(check.call_count, 4)

    def test_install_preserves_an_existing_default_hook(self):
        hook = self.root / ".git/hooks/pre-push"
        hook.write_text("# existing hook\n", encoding="utf-8")
        with patch.object(gate, "verify_tools"), self.assertRaisesRegex(gate.GateError, "existing default"):
            gate.install(self.root, sys.executable, sys.executable)
        self.assertEqual(hook.read_text(), "# existing hook\n")
        self.assertIsNone(gate.config_get(self.root, "extensions.worktreeConfig"))
        self.assertIsNone(gate.config_get(self.root, "core.hooksPath"))

    def test_install_preserves_a_different_hooks_path(self):
        self.git("config", "core.hooksPath", "existing-hooks")
        with patch.object(gate, "verify_tools"), self.assertRaisesRegex(gate.GateError, "Another core.hooksPath"):
            gate.install(self.root, sys.executable, sys.executable)
        self.assertEqual(gate.config_get(self.root, "core.hooksPath"), "existing-hooks")
        self.assertIsNone(gate.config_get(self.root, "extensions.worktreeConfig"))

    def test_install_settings_are_scoped_to_only_the_selected_worktree(self):
        self.git("config", "--unset", "petnote.checkPython")
        self.git("config", "--unset", "petnote.gitleaks")
        linked = self.base / "linked-worktree"
        self.git("worktree", "add", "-b", "review", str(linked))
        with patch.object(gate, "verify_tools"), contextlib.redirect_stdout(io.StringIO()):
            gate.install(linked, sys.executable, sys.executable)
            gate.install(linked, sys.executable, sys.executable)
        self.assertEqual(gate.config_get(linked, "extensions.worktreeConfig"), "true")
        self.assertEqual(gate.config_get(linked, "core.hooksPath", scope="--worktree"), ".githooks")
        self.assertEqual(Path(gate.config_get(linked, "petnote.checkPython", scope="--worktree")),
                         Path(sys.executable).absolute())
        self.assertEqual(Path(gate.config_get(linked, "petnote.gitleaks", scope="--worktree")),
                         Path(sys.executable).absolute())
        for name in ("core.hooksPath", "petnote.checkPython", "petnote.gitleaks"):
            self.assertIsNone(gate.config_get(self.root, name))
            self.assertIsNone(gate.config_get(linked, name, scope="--local"))

    def test_install_does_not_activate_dormant_worktree_config(self):
        (self.root / ".git/config.worktree").write_text("[core]\n\thooksPath = dormant-hooks\n", encoding="utf-8")
        with patch.object(gate, "verify_tools"), self.assertRaisesRegex(gate.GateError, "Dormant"):
            gate.install(self.root, sys.executable, sys.executable)
        self.assertIsNone(gate.config_get(self.root, "extensions.worktreeConfig"))

    def test_install_does_not_override_shared_core_worktree(self):
        self.git("config", "core.worktree", str(self.root))
        with patch.object(gate, "verify_tools"), self.assertRaisesRegex(gate.GateError, "core.worktree"):
            gate.install(self.root, sys.executable, sys.executable)
        self.assertIsNone(gate.config_get(self.root, "extensions.worktreeConfig"))

    def test_process_failures_withhold_output_and_missing_tools_fail_closed(self):
        for command in (
            [sys.executable, "-c", "print('synthetic-private-output'); raise SystemExit(2)"],
            [str(self.base / "missing-executable")],
        ):
            with self.subTest(command=command[0]), self.assertRaises(gate.GateError) as failure:
                gate.run_process(command, self.root, label="Synthetic check")
            self.assertNotIn("synthetic-private-output", str(failure.exception))

    def test_process_timeout_blocks_upload(self):
        with self.assertRaisesRegex(gate.GateError, "timed out"):
            gate.run_process([sys.executable, "-c", "import time; time.sleep(30)"],
                             self.root, label="Synthetic check", timeout=0.1)

    def test_tool_version_mismatch_is_blocked(self):
        with patch.object(gate, "run_process", side_effect=[(b"petnote-check-python\n", 0), (b"0.0.0\n", 0)]):
            with patch.object(gate.hashlib, "sha256") as digest:
                digest.return_value.hexdigest.return_value = gate.GITLEAKS_WINDOWS_SHA256
                with self.assertRaisesRegex(gate.GateError, "8.30.1"):
                    gate.verify_tools(self.root, Path(sys.executable), Path(sys.executable))

    @unittest.skipUnless(os.name == "nt", "Windows executable digest check")
    def test_unverified_windows_scanner_is_blocked_before_execution(self):
        with patch.object(gate, "run_process", return_value=(b"petnote-check-python\n", 0)) as process:
            with self.assertRaisesRegex(gate.GateError, "verified Windows release"):
                gate.verify_tools(self.root, Path(sys.executable), Path(sys.executable))
        self.assertEqual(process.call_count, 1)


class EnvironmentTests(unittest.TestCase):
    def test_scanner_environment_cannot_supply_an_allowlist(self):
        with patch.dict(os.environ, {"GITLEAKS_CONFIG": "synthetic-path", "GITLEAKS_CONFIG_TOML": "synthetic-config"}):
            environment = gate.command_env()
        self.assertFalse(any(key.upper().startswith("GITLEAKS_") for key in environment))
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_hook_repository_environment_does_not_escape_into_child_checks(self):
        with patch.dict(os.environ, {"GIT_DIR": "foreign", "GIT_WORK_TREE": "foreign", "GIT_INDEX_FILE": "foreign"}):
            environment = gate.command_env()
        self.assertNotIn("GIT_DIR", environment)
        self.assertNotIn("GIT_WORK_TREE", environment)
        self.assertNotIn("GIT_INDEX_FILE", environment)

    @unittest.skipIf(os.name == "nt", "POSIX virtualenv interpreter symlink")
    def test_virtualenv_interpreter_symlink_is_preserved(self):
        with TemporaryDirectory(prefix="petnote-python-test-") as directory:
            executable = Path(directory) / "venv/bin/python"
            executable.parent.mkdir(parents=True)
            executable.symlink_to(sys.executable)
            self.assertEqual(gate.executable_path(str(executable), "Python"), executable)

    def test_executable_path_must_be_existing_and_absolute(self):
        for value in (None, "", "python", "invalid\npath"):
            with self.subTest(value=value), self.assertRaises(gate.GateError):
                gate.executable_path(value, "Python")


if __name__ == "__main__":
    unittest.main()
