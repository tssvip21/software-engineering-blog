"""Regression checks for deployment packaging and the staged release boundary."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from deploy import deploy_vercel, vercel_build
from deploy.source_files import application_files, safe_source_file


CSS = b"body { color: #111; background: #fff; }\n"
HOME = '<!doctype html><title>펫노트</title><link rel="stylesheet" href="/static/care/app.css">'.encode()
HEADERS = {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "private, no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "strict-transport-security": "max-age=31536000",
    "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
}
PREVIOUS = "https://petnote-previous-petnote.vercel.app"
STAGED = "https://petnote-next-petnote.vercel.app"


def sources(root):
    entries = {
        ".python-version": b"3.13\n",
        "manage.py": b"# entry\n",
        "requirements.txt": b"-r requirements.lock\n",
        "requirements.lock": b"# fixture\n",
        "vercel.json": b'{"framework":"django"}',
        "deploy/vercel_build.py": b"# validation\n",
        "care/models.py": b"# model\n",
        "care/data/breeds.json": b"{}",
        "config/settings.py": b"# settings\n",
        "templates/care/home.html": HOME,
        "static/care/app.css": CSS,
        "static/care/fonts/Manrope-OFL.txt": b"license",
    }
    for name, content in entries.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return entries


def response(path):
    if path == "/health/":
        return 200, {"content-type": "application/json", "cache-control": "private, no-store"}, b'{"status":"ok"}'
    if path == "/":
        return 200, dict(HEADERS), HOME
    return 200, {"content-type": "text/css"}, CSS


def wire(result):
    status, headers, body = result
    head = "\r\n".join([f"HTTP/2 {status}", *[f"{key}: {value}" for key, value in headers.items()]])
    return head.encode() + b"\r\n\r\n" + body


class SourceTests(unittest.TestCase):
    def test_package_contains_required_runtime_files_and_excludes_local_data_and_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "application"
            expected = sources(root)
            for name in ("care/tests/test_private.py", "config/.env", "care/data/care.sqlite3",
                         "static/care/client_secret.json", "care/data/archive.dump", "config/private.key"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("local fixture", encoding="utf-8")
            package = Path(folder) / "package"
            hashes = deploy_vercel.package_sources(package, root=root)
            self.assertEqual(set(hashes), set(expected))
            self.assertEqual(list(hashes), sorted(hashes))
            self.assertEqual(hashes["static/care/app.css"], hashlib.sha256(CSS).hexdigest())
            self.assertEqual((package / "care/data/breeds.json").read_bytes(), b"{}")
            self.assertEqual((package / "requirements.txt").read_bytes(), expected["requirements.lock"])
            self.assertEqual(hashes["requirements.txt"], hashlib.sha256(expected["requirements.lock"]).hexdigest())
            self.assertEqual((root / "requirements.txt").read_bytes(), expected["requirements.txt"])

    def test_additional_requirement_directives_cannot_be_silently_dropped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "application"
            sources(root)
            for number, wrapper in enumerate((
                b"-r requirements.lock\nextra-package==1.0\n",
                b"-r requirements.lock\n-r other-requirements.txt\n",
                b"Django==5.2.18\n",
            )):
                with self.subTest(wrapper=wrapper):
                    (root / "requirements.txt").write_bytes(wrapper)
                    package = Path(folder) / f"package-{number}"
                    with self.assertRaises(deploy_vercel.DeploymentError):
                        deploy_vercel.package_sources(package, root=root)
                    self.assertFalse(package.exists())

    def test_traversal_and_missing_required_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in ("../outside.py", root / "absolute.py", "missing.py"):
                with self.subTest(relative=relative), self.assertRaises(ValueError):
                    safe_source_file(root, relative)

    def test_root_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "application"
            sources(root)
            external = Path(folder) / "external"
            external.write_bytes(b"private fixture")
            (root / "manage.py").unlink()
            try:
                (root / "manage.py").symlink_to(external)
            except OSError:
                self.skipTest("This Windows account cannot create symlinks.")
            with self.assertRaises(ValueError):
                deploy_vercel.package_sources(Path(folder) / "package", root=root)

    def test_application_directory_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "application"
            root.mkdir()
            external = Path(folder) / "external"
            external.mkdir()
            (external / "settings.py").write_bytes(b"private fixture")
            try:
                (root / "config").symlink_to(external, target_is_directory=True)
            except OSError:
                self.skipTest("This Windows account cannot create symlinks.")
            with self.assertRaises(ValueError):
                list(application_files(root))


class BuildTests(unittest.TestCase):
    def test_build_only_validates_even_if_old_initialization_flag_is_set(self):
        with mock.patch.dict(os.environ, {"VERCEL": "1", "PETNOTE_INITIALIZE_DB": "1"}, clear=True):
            with mock.patch.object(vercel_build.subprocess, "run") as run:
                vercel_build.main()
        commands = [call.args[0][2:] for call in run.call_args_list]
        self.assertEqual(commands, [["check", "--deploy", "--fail-level", "ERROR"], ["migrate", "--check", "--noinput"]])
        self.assertTrue(all(call.kwargs["check"] for call in run.call_args_list))

    def test_missing_schema_stops_the_build(self):
        with mock.patch.dict(os.environ, {"VERCEL": "1"}, clear=True):
            with mock.patch.object(vercel_build.subprocess, "run",
                                   side_effect=[None, subprocess.CalledProcessError(1, "schema check")]):
                with self.assertRaises(subprocess.CalledProcessError):
                    vercel_build.main()

    def test_accidental_local_execution_is_rejected(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(vercel_build.subprocess, "run") as run:
            with self.assertRaises(SystemExit):
                vercel_build.main()
        run.assert_not_called()


class ProbeTests(unittest.TestCase):
    def test_cli_prefix_and_proxy_headers_are_parsed(self):
        payload = b"unrelated Windows output\n" + wire((200, {}, b"")) + wire(response("/health/"))
        status, headers, body = deploy_vercel.parse_http_output(payload)
        self.assertEqual(status, 200)
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(json.loads(body), {"status": "ok"})
        self.assertEqual(deploy_vercel.parse_json_output(b"prefix\n{\"id\":\"prj_example\"}\n"), {"id": "prj_example"})

    def test_invalid_response_does_not_pass(self):
        with self.assertRaises(deploy_vercel.DeploymentError):
            deploy_vercel.parse_http_output(b"login screen")
        with self.assertRaises(deploy_vercel.DeploymentError):
            deploy_vercel.parse_json_output(b"not JSON")

    def test_only_plain_https_origins_and_vercel_deployment_urls_are_accepted(self):
        for value in ("http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com/?key=x"):
            with self.subTest(value=value), self.assertRaises(deploy_vercel.DeploymentError):
                deploy_vercel.public_origin(value)
        for value in ("evil.example", "https://petnote.vercel.app.evil.example", "https://user:pass@petnote.vercel.app",
                      "https://petnote.vercel.app/?key=x"):
            with self.subTest(value=value), self.assertRaises(deploy_vercel.DeploymentError):
                deploy_vercel.deployment_url(value)

    def test_health_security_and_packaged_css_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sources(root)
            self.assertEqual(len(deploy_vercel.smoke(response, root)), 3)
            for route, result in (
                ("/health/", (503, {}, b"")),
                ("/health/", (200, {"cache-control": "no-store"}, b'{"status":"error"}')),
                ("/", (200, {**HEADERS, "x-frame-options": "SAMEORIGIN"}, HOME)),
                ("/", (200, {**HEADERS, "cache-control": "public"}, HOME)),
                ("/static/care/app.css", (200, {"content-type": "text/css"}, b"stale CSS")),
            ):
                with self.subTest(route=route, result=result):
                    fetch = lambda path: result if path == route else response(path)
                    with self.assertRaises(deploy_vercel.DeploymentError):
                        deploy_vercel.smoke(fetch, root)

    def test_cli_error_output_is_not_exposed(self):
        failure = subprocess.CompletedProcess(["vercel"], 1, b"", b"synthetic-confidential-value")
        with mock.patch.object(deploy_vercel.subprocess, "run", return_value=failure):
            with self.assertRaises(deploy_vercel.DeploymentError) as raised:
                deploy_vercel.run_cli(["vercel"], ["api", "/v9/projects/petnote"])
        self.assertNotIn("synthetic-confidential-value", str(raised.exception))


class DiagnosticTests(unittest.TestCase):
    def test_failed_deploy_retains_cause_but_redacts_sensitive_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "application"
            logs = root / "tmp/release/logs"
            stdout = b"Preparing build\nDATABASE_URL=postgresql://demo:fixture-db@db.example.invalid/app\n"
            stderr = (b"Error: cannot resolve project settings\nGOOGLE_CLIENT_SECRET=fixture-client\n"
                      b"Authorization: Bearer fixture-auth\nSet-Cookie: session=fixture-cookie\n"
                      b"https://example.invalid/callback?code=fixture-code&state=fixture-state\n")
            failure = subprocess.CompletedProcess(["vercel"], 1, stdout, stderr)
            with mock.patch.object(deploy_vercel, "ROOT", root):
                with mock.patch.object(deploy_vercel.subprocess, "run", return_value=failure):
                    with self.assertRaises(deploy_vercel.DeploymentError) as raised:
                        deploy_vercel.run_cli(["vercel"], ["deploy"], diagnostic_dir=logs)
            entries = list(logs.glob("deploy-*.log"))
            self.assertEqual(len(entries), 1)
            saved = entries[0].read_text(encoding="utf-8")
            self.assertIn("Error: cannot resolve project settings", saved)
            self.assertIn(str(entries[0]), str(raised.exception))
            for value in ("fixture-db", "fixture-client", "fixture-auth", "fixture-cookie", "fixture-code", "fixture-state"):
                self.assertNotIn(value, saved)
                self.assertNotIn(value, str(raised.exception))

    def test_api_environment_and_authenticated_response_output_are_never_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "application"
            logs = root / "tmp/release/logs"
            failure = subprocess.CompletedProcess(["vercel"], 1, b'{"value":"confidential fixture"}', b"private detail")
            with mock.patch.object(deploy_vercel, "ROOT", root):
                with mock.patch.object(deploy_vercel.subprocess, "run", return_value=failure):
                    for command in ("api", "env", "curl", "link", "whoami"):
                        with self.subTest(command=command), self.assertRaises(deploy_vercel.DeploymentError):
                            deploy_vercel.run_cli(["vercel"], [command], diagnostic_dir=logs)
            self.assertFalse(logs.exists())

    def test_partial_build_output_is_preserved_on_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "application"
            logs = root / "tmp/release/logs"
            timeout = subprocess.TimeoutExpired(["vercel"], 1, output=b"Compiling application", stderr=b"Build timeout")
            with mock.patch.object(deploy_vercel, "ROOT", root):
                with mock.patch.object(deploy_vercel.subprocess, "run", side_effect=timeout):
                    with self.assertRaises(deploy_vercel.DeploymentError):
                        deploy_vercel.run_cli(["vercel"], ["build"], diagnostic_dir=logs)
            saved = next(logs.glob("build-*.log")).read_text(encoding="utf-8")
            self.assertIn("Compiling application", saved)
            self.assertIn("exit=timeout", saved)

    def test_diagnostics_cannot_be_written_outside_application_tmp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "application"
            outside = Path(directory) / "outside"
            with mock.patch.object(deploy_vercel, "ROOT", root), self.assertRaises(deploy_vercel.DeploymentError):
                deploy_vercel.save_cli_diagnostic(outside, "deploy", b"", b"", 1)
            self.assertFalse(outside.exists())


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "application"
        sources(self.root)
        self.calls = []
        self.promoted = False
        self.paused = False
        self.domain = "petnote.duckdns.org"
        self.bad_health = False
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.object(deploy_vercel, "ROOT", self.root))
        self.stack.enter_context(mock.patch.object(deploy_vercel, "cli_command", return_value=["vercel"]))
        self.stack.enter_context(mock.patch.object(deploy_vercel, "run_cli", side_effect=self.cli))
        self.stack.enter_context(mock.patch.object(deploy_vercel, "fetch_public", side_effect=lambda origin, path: response(path)))

    def cli(self, cli, arguments, **kwargs):
        self.calls.append(arguments)
        if arguments[0] == "api":
            if "/domains?" in arguments[1]:
                return json.dumps({"domains": [{"name": self.domain}]}).encode()
            return json.dumps({"id": "prj_example", "paused": self.paused, "rootDirectory": None,
                               "targets": {"production": {"url": STAGED if self.promoted else PREVIOUS}},
                               "env": [{"value": "synthetic-confidential-value"}]}).encode()
        if arguments[0] == "link":
            link = Path(kwargs["cwd"]) / ".vercel"
            link.mkdir()
            (link / "project.json").write_text("{\"projectId\":\"prj_example\"}", encoding="utf-8")
            return b""
        if arguments[0] == "deploy":
            pending = json.loads((self.root / "tmp/release/release.json").read_text(encoding="utf-8"))
            self.assertEqual(pending["status"], "deploying")
            self.assertEqual(pending["previous_deployment"], PREVIOUS)
            return b"Windows startup message\n" + STAGED.encode() + b"\n"
        if arguments[0] == "curl":
            self.assertNotIn("--project", arguments)
            self.assertEqual(Path(kwargs["cwd"]), self.root / "tmp/release/source")
            self.assertTrue((Path(kwargs["cwd"]) / ".vercel/project.json").is_file())
            if self.bad_health and arguments[1] == "/health/":
                return wire((503, {}, b""))
            return wire(response(arguments[1]))
        if arguments[0] == "promote":
            self.assertEqual(arguments, ["promote", STAGED, "--scope", "petnote", "--yes"])
            self.assertEqual(Path(kwargs["cwd"]), self.root / "tmp/release/source")
            self.assertTrue((Path(kwargs["cwd"]) / ".vercel/project.json").is_file())
            self.promoted = True
            return b""
        self.fail("Unexpected command that could change project settings.")

    def release(self, *arguments):
        output = self.root / "tmp/release"
        result = deploy_vercel.main(["--output", str(output), *arguments])
        manifest_text = (output / "release.json").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-confidential-value", manifest_text)
        return result, json.loads(manifest_text)

    def test_default_stages_and_verifies_without_promotion_or_environment_changes(self):
        result, manifest = self.release()
        self.assertEqual(result, 0)
        self.assertEqual(manifest["status"], "verified_staged")
        self.assertFalse(self.promoted)
        deploy = next(call for call in self.calls if call[0] == "deploy")
        self.assertIn("--prod", deploy)
        self.assertIn("--skip-domain", deploy)
        self.assertNotIn("--build-env", deploy)
        self.assertEqual(manifest["previous_deployment"], PREVIOUS)
        self.assertIn(PREVIOUS, manifest["rollback_command"])
        self.assertNotIn("--project", manifest["rollback_command"])
        self.assertEqual(manifest["rollback_command"][-1], "--yes")
        self.assertEqual(Path(manifest["rollback_cwd"]), self.root / "tmp/release/source")

    def test_promotion_requires_flag_and_verifies_current_production(self):
        result, manifest = self.release("--promote")
        self.assertEqual(result, 0)
        self.assertTrue(self.promoted)
        self.assertEqual(manifest["status"], "promoted")
        self.assertEqual(len(manifest["public_checks"]), 3)

    def test_bad_stage_never_promotes(self):
        self.bad_health = True
        result, manifest = self.release("--promote")
        self.assertEqual(result, 1)
        self.assertEqual(manifest["failed_at"], "staged")
        self.assertFalse(self.promoted)

    def test_wrong_domain_never_uploads(self):
        self.domain = "different.example"
        result, manifest = self.release()
        self.assertEqual(result, 1)
        self.assertEqual(manifest["failed_at"], "packaged")
        self.assertFalse(any(call[0] == "deploy" for call in self.calls))

    def test_paused_project_never_uploads(self):
        self.paused = True
        result, _ = self.release()
        self.assertEqual(result, 1)
        self.assertFalse(any(call[0] == "deploy" for call in self.calls))

    def test_package_only_never_contacts_vercel(self):
        result, manifest = self.release("--package-only")
        self.assertEqual(result, 0)
        self.assertEqual(manifest["status"], "packaged")
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
