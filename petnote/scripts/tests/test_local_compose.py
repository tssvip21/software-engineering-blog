"""Keep local Compose isolated from shell credentials and unsafe saved settings."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "petnote_local_compose", Path(__file__).resolve().parents[1] / "local_compose.py",
)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class LocalComposeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.project = "petnote-local-tool-test"
        self.port = 18096
        self.envfile = self.root / "tmp/local-docker" / self.project / "runtime.env"
        self.addCleanup(patch.stopall)
        patch.object(runner, "ROOT", self.root).start()
        patch.dict(os.environ, {}, clear=True).start()
        patch.object(runner.shutil, "which", return_value="docker").start()

    def saved_settings(self):
        runner.create_settings(self.envfile, self.port)
        return runner.read_settings(self.envfile, self.port)

    def write_settings(self, settings):
        self.envfile.write_text(
            "\n".join(f"{key}={value}" for key, value in settings.items()) + "\n",
            encoding="utf-8",
        )

    def call(self, action):
        return runner.main([action, "--project", self.project, "--port", str(self.port)])

    @staticmethod
    def successful_run(command, **kwargs):
        if command[1:3] == ["context", "inspect"]:
            return subprocess.CompletedProcess(command, 0, "unix:///var/run/docker.sock\n")
        return subprocess.CompletedProcess(command, 0, "linux\n" if "info" in command else "")

    def test_shell_cannot_override_validated_local_configuration(self):
        settings = self.saved_settings()
        contaminated = {
            "PATH": "test-path", "DOCKER_CONTEXT": "desktop-linux",
            "DOCKER_HOST": "tcp://example.invalid:2376",
            "HTTP_BIND": "0.0.0.0", "HTTPS_BIND": "0.0.0.0",
            "HTTP_PORT": "80", "HTTPS_PORT": "443",
            "APP_ORIGIN": "https://example.invalid",
            "POSTGRES_PASSWORD": "parent-placeholder",
            "DJANGO_SECRET_KEY": "parent-placeholder",
            "GOOGLE_CLIENT_ID": "parent-placeholder",
            "GOOGLE_CLIENT_SECRET": "parent-placeholder",
            "DATABASE_URL": "postgresql://example.invalid/example",
            "CARE_PUBLIC_DEMO_ENTRY": "1",
            "COMPOSE_ENV_FILES": "unrelated.env", "COMPOSE_PROFILES": "unrelated",
            "COMPOSE_PROJECT_NAME": "unrelated", "COMPOSE_FILE": "unrelated.yaml",
        }
        os.environ.update(contaminated)
        with patch.object(runner.subprocess, "run", side_effect=self.successful_run) as run:
            self.assertEqual(self.call("check"), 0)
        command, = run.call_args.args
        env = run.call_args.kwargs["env"]
        for key, value in settings.items():
            self.assertEqual(env[key], value, key)
        self.assertEqual(env["CARE_PUBLIC_DEMO_ENTRY"], "0")
        self.assertEqual(env["PATH"], "test-path")
        self.assertEqual(env["DOCKER_HOST"], "unix:///var/run/docker.sock")
        self.assertNotIn("DOCKER_CONTEXT", env)
        for key in ("DATABASE_URL", "COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_ENV_FILES", "COMPOSE_PROJECT_NAME"):
            self.assertNotIn(key, env)
        self.assertEqual(command[-2:], ["config", "--quiet"])
        self.assertEqual(dict(os.environ), contaminated)

    def test_setting_names_are_case_insensitive_when_filtering_parent_environment(self):
        mixed = {"http_bind": "0.0.0.0", "Postgres_Password": "parent-placeholder", "Path": "retained"}
        with patch.object(runner.os, "environ", mixed):
            self.assertEqual(runner.isolated_environment(), {"Path": "retained"})

    def test_all_actions_preserve_existing_settings_and_never_remove_volumes(self):
        self.saved_settings()
        original = self.envfile.read_bytes()
        for action in ("up", "stop", "down", "status", "check", "test"):
            with self.subTest(action=action):
                with patch.object(runner.subprocess, "run", side_effect=self.successful_run) as run:
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(self.call(action), 0)
                command, = run.call_args.args
                self.assertNotIn("--volumes", command)
                self.assertEqual(command[command.index("-p") + 1], self.project)
                self.assertEqual(command[command.index("--env-file") + 1], str(self.envfile))
                self.assertEqual(self.envfile.read_bytes(), original)

    def test_unsafe_saved_settings_fail_before_any_compose_action(self):
        settings = self.saved_settings()
        unsafe = {
            "HTTP_BIND": "0.0.0.0", "HTTPS_BIND": "0.0.0.0",
            "HTTP_PORT": "80", "HTTPS_PORT": "443",
            "DJANGO_DEBUG": "0", "POSTGRES_DB": "unrelated", "POSTGRES_USER": "unrelated",
            "SITE_ADDRESS": "https://example.invalid", "APP_ORIGIN": "https://example.invalid",
            "DJANGO_ALLOWED_HOSTS": "*", "GOOGLE_CLIENT_ID": "external-placeholder",
            "GOOGLE_CLIENT_SECRET": "external-placeholder",
            "DJANGO_SECRET_KEY": "", "POSTGRES_PASSWORD": "",
        }
        for key, value in unsafe.items():
            with self.subTest(setting=key):
                self.write_settings(settings | {key: value})
                original = self.envfile.read_bytes()
                with patch.object(runner.subprocess, "run", side_effect=self.successful_run) as run:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        self.call("up")
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(any(call.args[0][1] == "compose" for call in run.call_args_list))
                self.assertEqual(self.envfile.read_bytes(), original)

    def test_unknown_or_missing_saved_keys_are_rejected(self):
        settings = self.saved_settings()
        missing = dict(settings)
        missing.pop("HTTPS_BIND")
        for invalid in (settings | {"CARE_PUBLIC_DEMO_ENTRY": "1"}, missing):
            self.write_settings(invalid)
            with self.assertRaises(ValueError):
                runner.read_settings(self.envfile, self.port)

    def test_duplicate_setting_is_not_silently_overridden(self):
        self.saved_settings()
        with self.envfile.open("a", encoding="utf-8") as output:
            output.write("HTTP_BIND=0.0.0.0\n")
        with self.assertRaises(ValueError):
            runner.read_settings(self.envfile, self.port)

    def test_invalid_line_is_rejected_without_echoing_its_value(self):
        self.saved_settings()
        marker = "private-placeholder-without-assignment"
        with self.envfile.open("a", encoding="utf-8") as output:
            output.write(marker + "\n")
        with self.assertRaises(ValueError) as error:
            runner.read_settings(self.envfile, self.port)
        self.assertNotIn(marker, str(error.exception))

    def test_create_does_not_overwrite_an_existing_environment(self):
        self.saved_settings()
        original = self.envfile.read_bytes()
        with self.assertRaises(FileExistsError):
            runner.create_settings(self.envfile, self.port)
        self.assertEqual(self.envfile.read_bytes(), original)

    @unittest.skipIf(os.name == "nt", "POSIX permission bits are checked by Linux CI.")
    def test_new_environment_is_private_from_creation(self):
        self.saved_settings()
        self.assertEqual(stat.S_IMODE(self.envfile.stat().st_mode), 0o600)

    def test_non_start_action_does_not_create_environment(self):
        with patch.object(runner.subprocess, "run", side_effect=self.successful_run) as run:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call("status")
        self.assertFalse(any(call.args[0][1] == "compose" for call in run.call_args_list))
        self.assertFalse(self.envfile.exists())

    def test_busy_port_prevents_new_environment_creation(self):
        with patch.object(runner.subprocess, "run", side_effect=self.successful_run) as run:
            with patch.object(runner, "ensure_ports_available", side_effect=runner.LocalComposeError("occupied")):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    self.call("up")
        self.assertFalse(any(call.args[0][1] == "compose" for call in run.call_args_list))
        self.assertFalse(self.envfile.exists())

    def test_compose_failure_is_returned(self):
        self.saved_settings()
        def failing_compose(command, **kwargs):
            if command[1] == "compose":
                return subprocess.CompletedProcess(command, 7)
            return self.successful_run(command, **kwargs)
        with patch.object(runner.subprocess, "run", side_effect=failing_compose):
            self.assertEqual(self.call("check"), 7)


    def test_remote_host_is_rejected_before_any_daemon_request(self):
        for endpoint in ("tcp://example.invalid:2376", "ssh://example.invalid", "npipe:////other-host/pipe/docker_engine"):
            with self.subTest(endpoint=endpoint):
                os.environ["DOCKER_HOST"] = endpoint
                with patch.object(runner.subprocess, "run") as run:
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        self.call("up")
                run.assert_not_called()
                self.assertFalse(self.envfile.exists())

    def test_remote_context_is_rejected_before_daemon_request(self):
        os.environ["DOCKER_CONTEXT"] = "remote-example"
        os.environ["DOCKER_HOST"] = "unix:///var/run/docker.sock"
        response = subprocess.CompletedProcess([], 0, "ssh://example.invalid\n")
        with patch.object(runner.subprocess, "run", return_value=response) as run:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call("up")
        self.assertEqual(run.call_args.args[0][1:3], ["context", "inspect"])
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.envfile.exists())

    def test_explicit_local_host_precedes_the_saved_default_context(self):
        os.environ["DOCKER_HOST"] = "unix:///run/user/1000/docker.sock"
        with patch.object(runner.subprocess, "run") as run:
            env = runner.local_docker_environment("docker")
        run.assert_not_called()
        self.assertEqual(env["DOCKER_HOST"], os.environ["DOCKER_HOST"])

    def test_local_named_pipe_is_allowed_and_pinned(self):
        response = subprocess.CompletedProcess([], 0, "npipe:////./pipe/dockerDesktopLinuxEngine\n")
        with patch.object(runner.subprocess, "run", return_value=response):
            env = runner.local_docker_environment("docker")
        self.assertEqual(env["DOCKER_HOST"], response.stdout.strip())
        self.assertNotIn("DOCKER_CONTEXT", env)

    def test_existing_project_without_env_file_preserves_all_resources(self):
        for resource in ("container", "volume"):
            with self.subTest(resource=resource):
                def existing(command, **kwargs):
                    if command[1] == resource:
                        return subprocess.CompletedProcess(command, 0, "existing-resource\n")
                    return self.successful_run(command, **kwargs)
                with patch.object(runner.subprocess, "run", side_effect=existing) as run:
                    errors = io.StringIO()
                    with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit):
                        self.call("up")
                self.assertIn("runtime.env", errors.getvalue())
                self.assertFalse(self.envfile.exists())
                self.assertFalse(any(call.args[0][1] == "compose" for call in run.call_args_list))
                listings = [call.args[0] for call in run.call_args_list if call.args[0][1] in ("container", "volume")]
                self.assertTrue(listings)
                for command in listings:
                    self.assertIn(f"label=com.docker.compose.project={self.project}", command)
                if resource == "container":
                    self.assertIn("--all", listings[0])

    def test_resource_lookup_error_does_not_generate_credentials(self):
        def failed_listing(command, **kwargs):
            if command[1] == "volume":
                return subprocess.CompletedProcess(command, 1, "", "not logged")
            return self.successful_run(command, **kwargs)
        with patch.object(runner.subprocess, "run", side_effect=failed_listing) as run:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.call("up")
        self.assertFalse(self.envfile.exists())
        self.assertFalse(any(call.args[0][1] == "compose" for call in run.call_args_list))

    def test_clean_new_project_creates_one_reusable_environment(self):
        with patch.object(runner.subprocess, "run", side_effect=self.successful_run):
            with patch.object(runner, "ensure_ports_available"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(self.call("up"), 0)
        settings = runner.read_settings(self.envfile, self.port)
        self.assertEqual(settings["HTTP_BIND"], "127.0.0.1")
        self.assertNotEqual(settings["POSTGRES_PASSWORD"], settings["DJANGO_SECRET_KEY"])

    def test_quoted_or_interpolated_secret_is_not_silently_changed(self):
        settings = self.saved_settings()
        for value in ("'placeholder'", '"placeholder"', "$" + "{FROM_SHELL}", "placeholder # comment"):
            self.write_settings(settings | {"POSTGRES_PASSWORD": value})
            original = self.envfile.read_bytes()
            with self.assertRaises(ValueError):
                runner.read_settings(self.envfile, self.port)
            self.assertEqual(self.envfile.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
