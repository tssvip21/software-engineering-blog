"""Regression checks for the local Vercel manager; no network or real credentials."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
from email.message import Message
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import vercel_control as control

SECRET = "test-only-sensitive-value"
DEPLOYMENT = "dpl_ExampleDeployment123"


def project(paused=False, **changes):
    result = {
        "id": "prj_TestProject", "name": control.PROJECT_NAME,
        "accountId": "team_TestTeam", "paused": paused,
        "targets": {"production": {"id": DEPLOYMENT}},
        "env": [{"value": SECRET}],
    }
    result.update(changes)
    return result


def response(value, *, code=0, stderr=b""):
    stdout = value if isinstance(value, bytes) else json.dumps(value).encode()
    return subprocess.CompletedProcess(["fake-vercel"], code, stdout, stderr)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        link = Path(tmp.name) / "project.json"
        link.write_text(json.dumps({"projectId": "prj_TestProject", "orgId": "team_TestTeam"}), encoding="utf-8")
        link_patch = patch.object(control, "LINK_FILE", link)
        link_patch.start()
        self.addCleanup(link_patch.stop)
        self.lock = patch.object(control, "operation_lock", side_effect=lambda: nullcontext())
        self.lock.start()
        self.addCleanup(self.lock.stop)

    def controller(self, answers, *, health=(200, False), clock=None):
        runner = Mock(side_effect=answers)
        probe = Mock(return_value=health)
        sleeper = Mock()
        controller = control.VercelController(
            cli=["fake-node", "fake-vercel"], runner=runner, probe=probe,
            clock=clock or (lambda: 0), sleeper=sleeper,
        )
        return controller, runner, probe, sleeper

    @staticmethod
    def methods(runner):
        return [call.args[0][call.args[0].index("--method") + 1] for call in runner.call_args_list]

    def test_missing_or_invalid_local_link_stops_before_request(self):
        control.LINK_FILE.unlink()
        runner = Mock()
        for contents in (None, "[]", '{"projectId":"../other","orgId":"team_TestTeam"}'):
            if contents is not None:
                control.LINK_FILE.write_text(contents, encoding="utf-8")
            with self.assertRaises(control.ControlError) as caught:
                control.VercelController(cli=["fake-vercel"], runner=runner)
            self.assertEqual(caught.exception.kind, "setup")
        runner.assert_not_called()

    def test_identity_fields_are_verified_before_writes(self):
        for field in ("id", "name", "accountId"):
            with self.subTest(field=field):
                client, runner, _, _ = self.controller([response(project(**{field: "another-target"}))])
                with self.assertRaises(control.ControlError) as caught:
                    client.set_paused(True)
                self.assertEqual(caught.exception.kind, "target")
                self.assertEqual(self.methods(runner), ["GET"])

    def test_paused_requires_an_explicit_boolean(self):
        for value in (None, "false", 0, 1, []):
            with self.subTest(value=value):
                client, runner, _, _ = self.controller([response(project(paused=value))])
                with self.assertRaises(control.ControlError) as caught:
                    client.set_paused(True)
                self.assertEqual(caught.exception.kind, "response")
                self.assertEqual(self.methods(runner), ["GET"])
        missing = project()
        del missing["paused"]
        client, _, _, _ = self.controller([response(missing)])
        with self.assertRaises(control.ControlError):
            client.project()

    def test_missing_or_malformed_production_fails_closed(self):
        for targets in (
            None, [], {}, {"production": None}, {"production": []},
            {"production": {}}, {"production": {"id": None}},
            {"production": {"id": ""}}, {"production": {"id": "other"}},
            {"production": {"id": "dpl_../../other"}},
        ):
            with self.subTest(targets=targets):
                client, runner, _, _ = self.controller([response(project(targets=targets))])
                with self.assertRaises(control.ControlError) as caught:
                    client.set_paused(True)
                self.assertEqual(caught.exception.kind, "response")
                self.assertEqual(self.methods(runner), ["GET"])

    def test_status_discards_sensitive_project_fields(self):
        client, runner, _, _ = self.controller([response(project())])
        state = client.status()
        self.assertEqual(
            asdict(state),
            {"paused": False, "health_status": 200, "public_paused": False, "production_id": DEPLOYMENT},
        )
        self.assertNotIn(SECRET, repr(state))
        self.assertEqual(self.methods(runner), ["GET"])

    def test_unapproved_requests_never_reach_cli(self):
        client, runner, _, _ = self.controller([])
        for method, path in (
            ("DELETE", client.project_path), ("POST", client.project_path),
            ("GET", "/v9/projects/other"), ("POST", client.action_paths[True] + "&other=1"),
        ):
            with self.subTest(method=method, path=path):
                with self.assertRaises(control.ControlError) as caught:
                    client._api(method, path)
                self.assertEqual(caught.exception.kind, "target")
        runner.assert_not_called()

    def test_cli_uses_saved_auth_and_cannot_prompt(self):
        client, runner, _, _ = self.controller([response(project())])
        with patch.dict(control.os.environ, {
            "VERCEL_TOKEN": SECRET, "VERCEL_ORG_ID": "wrong",
            "VERCEL_PROJECT_ID": "wrong", "FORCE_COLOR": "1",
        }):
            client.project()
            self.assertEqual(control.os.environ["VERCEL_TOKEN"], SECRET)
        command = runner.call_args.args[0]
        options = runner.call_args.kwargs
        self.assertEqual(command[3], client.project_path)
        self.assertIn("--non-interactive", command)
        self.assertIn("--raw", command)
        self.assertEqual(command[command.index("--scope") + 1], control.SCOPE)
        self.assertNotIn("--token", command)
        self.assertEqual(options["stdin"], subprocess.DEVNULL)
        self.assertTrue(options["capture_output"])
        self.assertGreater(options["timeout"], 0)
        self.assertNotIn("VERCEL_TOKEN", options["env"])
        self.assertNotIn("VERCEL_ORG_ID", options["env"])
        self.assertNotIn("VERCEL_PROJECT_ID", options["env"])
        self.assertNotIn(SECRET, repr(command))

    def test_failed_cli_responses_have_safe_classified_errors(self):
        for marker, kind in (
            ("401 invalid_token", "auth"), ("No existing credentials", "auth"),
            ("403 Forbidden", "permission"), ("429 rate limit", "rate_limit"),
            ("connection failed", "request"),
        ):
            with self.subTest(marker=marker):
                failed = response((SECRET + marker).encode(), code=1, stderr=SECRET.encode())
                client, _, _, _ = self.controller([failed])
                with self.assertRaises(control.ControlError) as caught:
                    client.project()
                self.assertEqual(caught.exception.kind, kind)
                self.assertNotIn(SECRET, str(caught.exception))

    def test_malformed_and_error_json_does_not_leak_response(self):
        for data in (
            (SECRET + " not json").encode(), b"\xff", [], SECRET,
            {"error": {"message": SECRET}}, {"error": None},
        ):
            with self.subTest(data=type(data).__name__):
                client, _, _, _ = self.controller([response(data)])
                with self.assertRaises(control.ControlError) as caught:
                    client.project()
                self.assertEqual(caught.exception.kind, "response")
                self.assertNotIn(SECRET, str(caught.exception))

    def test_timeout_hides_captured_diagnostics(self):
        failure = subprocess.TimeoutExpired(["fake-vercel"], 45, output=SECRET.encode(), stderr=SECRET.encode())
        client, _, _, _ = self.controller([failure])
        with self.assertRaises(control.ControlError) as caught:
            client.project()
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_process_start_failure_hides_os_details(self):
        client, _, _, _ = self.controller([OSError(SECRET)])
        with self.assertRaises(control.ControlError) as caught:
            client.project()
        self.assertEqual(caught.exception.kind, "setup")
        self.assertNotIn(SECRET, str(caught.exception))

    def test_already_running_does_not_send_start(self):
        client, runner, _, sleeper = self.controller([response(project()), response(project())])
        self.assertTrue(client.set_paused(False).confirmed)
        self.assertEqual(self.methods(runner), ["GET", "GET"])
        sleeper.assert_not_called()

    def test_already_paused_does_not_send_stop(self):
        client, runner, _, sleeper = self.controller(
            [response(project(True)), response(project(True))], health=(503, True),
        )
        self.assertTrue(client.set_paused(True).confirmed)
        self.assertEqual(self.methods(runner), ["GET", "GET"])
        sleeper.assert_not_called()

    def test_start_is_sent_once_and_waits_for_public_recovery(self):
        client, runner, probe, sleeper = self.controller([
            response(project(True)), response({}), response(project()), response(project()),
        ])
        probe.side_effect = [(503, True), (200, False)]
        state = client.set_paused(False)
        self.assertFalse(state.paused)
        self.assertTrue(state.confirmed)
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET", "GET"])
        self.assertEqual(runner.call_args_list[1].args[0][3], client.action_paths[False])
        sleeper.assert_called_once_with(3)

    def test_stop_is_sent_once_and_waits_for_public_pause(self):
        client, runner, probe, sleeper = self.controller([
            response(project()), response({}), response(project(True)), response(project(True)),
        ])
        probe.side_effect = [(200, False), (503, True)]
        state = client.set_paused(True)
        self.assertTrue(state.paused)
        self.assertTrue(state.confirmed)
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET", "GET"])
        self.assertEqual(runner.call_args_list[1].args[0][3], client.action_paths[True])
        sleeper.assert_called_once_with(3)

    def test_database_503_does_not_confirm_a_pause(self):
        clock = Mock(side_effect=[0, 1])
        client, runner, _, _ = self.controller(
            [response(project(True)), response(project(True))],
            health=(503, False), clock=clock,
        )
        with self.assertRaises(control.ControlError) as caught:
            client.set_paused(True, timeout=0)
        self.assertEqual(caught.exception.kind, "pending")
        self.assertEqual(self.methods(runner), ["GET", "GET"])

    def test_confirmation_requires_matching_public_and_control_state(self):
        for paused, status, marker, expected in (
            (False, 200, False, True), (True, 503, True, True),
            (False, 503, True, False), (True, 200, False, False),
            (True, 503, False, False), (False, None, False, False),
            (False, 200, True, False), (True, None, False, False),
        ):
            with self.subTest(paused=paused, status=status, marker=marker):
                self.assertEqual(control.ServerState(paused, status, marker, DEPLOYMENT).confirmed, expected)

    def test_timed_out_post_reconciles_without_duplicate_post(self):
        failure = subprocess.TimeoutExpired(["fake-vercel"], 45)
        client, runner, _, _ = self.controller([
            response(project()), failure, response(project(True)), response(project(True)),
        ], health=(503, True))
        self.assertTrue(client.set_paused(True).confirmed)
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET", "GET"])

    def test_ambiguous_post_is_not_retried_when_state_is_unchanged(self):
        failure = subprocess.TimeoutExpired(["fake-vercel"], 45)
        client, runner, _, _ = self.controller([response(project()), failure, response(project())])
        with self.assertRaises(control.ControlError) as caught:
            client.set_paused(True)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET"])

    def test_reconciliation_failure_keeps_original_safe_error(self):
        client, runner, _, _ = self.controller([
            response(project()), subprocess.TimeoutExpired(["fake-vercel"], 45), OSError(SECRET),
        ])
        with self.assertRaises(control.ControlError) as caught:
            client.set_paused(True)
        self.assertEqual(caught.exception.kind, "timeout")
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET"])

    def test_denied_post_is_not_repeated(self):
        client, runner, _, _ = self.controller([
            response(project()), response(b"403 Forbidden", code=1),
        ])
        with self.assertRaises(control.ControlError) as caught:
            client.set_paused(True)
        self.assertEqual(caught.exception.kind, "permission")
        self.assertEqual(self.methods(runner), ["GET", "POST"])

    def test_failed_initial_read_prevents_mutation(self):
        client, runner, _, _ = self.controller([response(b"401 invalid_token", code=1)])
        with self.assertRaises(control.ControlError):
            client.set_paused(True)
        self.assertEqual(self.methods(runner), ["GET"])

    def test_propagation_deadline_does_not_report_success(self):
        client, runner, _, _ = self.controller([
            response(project()), response({}), response(project()),
        ], health=(503, True), clock=Mock(side_effect=[0, 1]))
        with self.assertRaises(control.ControlError) as caught:
            client.set_paused(True, timeout=0)
        self.assertEqual(caught.exception.kind, "pending")
        self.assertEqual(self.methods(runner), ["GET", "POST", "GET"])

    def test_non_boolean_action_is_rejected_before_any_request(self):
        client, runner, _, _ = self.controller([])
        for value in ("false", None, 1, "stop"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    client.set_paused(value)
        runner.assert_not_called()


class LocalSafetyTests(unittest.TestCase):
    def test_public_probe_uses_anonymous_head(self):
        reply = Mock(status=200, headers={})
        reply.__enter__ = Mock(return_value=reply)
        reply.__exit__ = Mock(return_value=False)
        opener = Mock()
        opener.open.return_value = reply
        with patch.object(control, "build_opener", return_value=opener) as builder:
            self.assertEqual(control.public_health(), (200, False))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, control.PUBLIC_ORIGIN + "/health/")
        self.assertEqual(request.get_method(), "HEAD")
        self.assertNotIn("Authorization", request.headers)
        self.assertNotIn("Cookie", request.headers)
        self.assertTrue(any(
            handler is control.NoRedirects or isinstance(handler, control.NoRedirects)
            for handler in builder.call_args.args
        ))

    def test_public_probe_never_follows_redirects(self):
        handler = control.NoRedirects()
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, "https://example.invalid/"))

    def test_public_probe_distinguishes_paused_from_other_failure(self):
        for code, marker, expected in (
            (503, "DEPLOYMENT_PAUSED", (503, True)), (503, "", (503, False)),
            (502, "", (502, False)), (302, "", (302, False)),
        ):
            with self.subTest(code=code, marker=marker):
                headers = Message()
                headers["x-vercel-error"] = marker
                error = HTTPError(control.PUBLIC_ORIGIN, code, "test", headers, io.BytesIO())
                opener = Mock()
                opener.open.side_effect = error
                with patch.object(control, "build_opener", return_value=opener):
                    self.assertEqual(control.public_health(), expected)
        opener = Mock()
        opener.open.side_effect = URLError(SECRET)
        with patch.object(control, "build_opener", return_value=opener):
            self.assertEqual(control.public_health(), (None, False))

    def test_mutation_lock_blocks_second_owner_and_releases(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(control, "ROOT", Path(directory)):
            with control.operation_lock():
                with self.assertRaises(control.ControlError) as caught:
                    with control.operation_lock():
                        self.fail("A second owner acquired the mutation lock.")
                self.assertEqual(caught.exception.kind, "busy")
            with control.operation_lock():
                pass

    def test_cache_selection_requires_pinned_version_and_contained_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package = base / "npm-cache" / "_npx" / "sample" / "node_modules" / "vercel"
            package.mkdir(parents=True)
            entry = package / "dist" / "vc.js"
            entry.parent.mkdir()
            entry.write_text("// test-only launcher", encoding="utf-8")
            (package / "package.json").write_text(json.dumps({
                "name": "vercel", "version": control.CLI_VERSION, "bin": {"vercel": "dist/vc.js"},
            }), encoding="utf-8")
            with patch.dict(control.os.environ, {"LOCALAPPDATA": directory}, clear=True), \
                    patch.object(control.shutil, "which", return_value="fake-node"), \
                    patch.object(control.Path, "home", return_value=base / "home"), \
                    patch.object(control, "cli_command") as fallback:
                self.assertEqual(control.find_cli(), ["fake-node", str(entry.resolve())])
                fallback.assert_not_called()

    def test_corrupt_cache_metadata_falls_back_safely(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package = base / "npm-cache" / "_npx" / "sample" / "node_modules" / "vercel"
            package.mkdir(parents=True)
            metadata = package / "package.json"
            for value in (
                [], "invalid",
                {"name": "vercel", "version": control.CLI_VERSION, "bin": []},
                {"name": "vercel", "version": control.CLI_VERSION, "bin": {"vercel": 7}},
                {"name": "vercel", "version": "old", "bin": {"vercel": "dist/vc.js"}},
            ):
                with self.subTest(value=value):
                    metadata.write_text(json.dumps(value), encoding="utf-8")
                    with patch.dict(control.os.environ, {"LOCALAPPDATA": directory}, clear=True), \
                            patch.object(control.shutil, "which", return_value="fake-node"), \
                            patch.object(control.Path, "home", return_value=base / "home"), \
                            patch.object(control, "cli_command", return_value=["fake-fallback"]) as fallback:
                        self.assertEqual(control.find_cli(), ["fake-fallback"])
                        fallback.assert_called_once()

    def test_cache_binary_cannot_escape_package_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            package = base / "npm-cache" / "_npx" / "sample" / "node_modules" / "vercel"
            package.mkdir(parents=True)
            outside = package.parent / "outside.js"
            outside.write_text("// test-only outside file", encoding="utf-8")
            (package / "package.json").write_text(json.dumps({
                "name": "vercel", "version": control.CLI_VERSION, "bin": {"vercel": "../outside.js"},
            }), encoding="utf-8")
            with patch.dict(control.os.environ, {"LOCALAPPDATA": directory}, clear=True), \
                    patch.object(control.shutil, "which", return_value="fake-node"), \
                    patch.object(control.Path, "home", return_value=base / "home"), \
                    patch.object(control, "cli_command", return_value=["fake-fallback"]):
                self.assertEqual(control.find_cli(), ["fake-fallback"])


if __name__ == "__main__":
    unittest.main()
