"""Regression checks for publication guards; fixtures contain only synthetic values."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import check_privacy as guard


class TextRulesTests(unittest.TestCase):
    def test_public_examples_and_identifiers_are_allowed(self):
        self.assertEqual(guard.text_findings('dev@example.invalid create read README'), set())

    def test_personal_email_and_home_path_are_detected(self):
        email = 'person@' + 'gmail.com'
        directory = 'C:' + '/Users/' + 'synthetic'
        self.assertIn('personal email', guard.text_findings(email))
        self.assertIn('personal home directory', guard.text_findings(directory))

    def test_access_token_is_detected_without_logging_it(self):
        token = 'gh' + 'p_' + 'a' * 36
        with contextlib.redirect_stdout(io.StringIO()) as output:
            findings = guard.text_findings(token)
        self.assertIn('private key or access token', findings)
        self.assertNotIn(token, output.getvalue())

    def test_internal_vocabulary_requires_word_boundary(self):
        term = ''.join(chr(c) for c in (82, 69, 65))
        self.assertIn('excluded public wording', guard.text_findings(term))
        self.assertNotIn('excluded public wording', guard.text_findings('CREATE README read'))

    def test_korean_wording_guard_preserves_ordinary_words(self):
        ordinary = ''.join(chr(c) for c in (0xC881, 0xC740))
        blocked = chr(0xC886)
        self.assertNotIn('excluded public wording', guard.text_findings(ordinary))
        self.assertIn('excluded public wording', guard.text_findings(blocked))

    def test_private_filenames_are_blocked(self):
        for name in ['.env', '.env.production', '.local_secret', 'db.sqlite3', 'backup.dump', 'signing.key']:
            with self.subTest(name=name):
                self.assertTrue(guard.private_filename(name))

    def test_configuration_examples_are_allowed(self):
        for name in ['.env.example', '.env.local.example', 'production.env.example', 'config/settings.py']:
            with self.subTest(name=name):
                self.assertFalse(guard.private_filename(name))

    def test_pull_request_uses_head_and_base_from_event(self):
        event = {'pull_request': {'base': {'sha': 'a' * 40}, 'head': {'sha': 'b' * 40}}}
        self.assertEqual(guard.revision_range(event, 'c' * 40), ('a' * 40, 'b' * 40))

    def test_manual_dispatch_uses_current_commit_parent(self):
        with patch.object(guard, 'git', return_value=b'parent second-parent\n'):
            self.assertEqual(guard.revision_range({}, 'head'), ('parent', 'head'))

    def test_new_branch_keeps_complete_history_scan(self):
        self.assertEqual(guard.revision_range({'before': '0' * 40}, 'head'), ('0' * 40, 'head'))


class GitHistoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.previous = Path.cwd()
        os.chdir(self.folder.name)
        self.run_git('init', '-q')
        self.run_git('config', 'user.name', 'CI Test')
        self.run_git('config', 'user.email', 'ci@example.invalid')

    def tearDown(self):
        os.chdir(self.previous)
        self.folder.cleanup()

    def run_git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.STDOUT).decode().strip()

    def commit(self, filename, text):
        path = Path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        self.run_git('add', '--', filename)
        self.run_git('commit', '-qm', 'synthetic test')
        return self.run_git('rev-parse', 'HEAD')

    def test_added_secret_fails_and_deletion_does_not_repeat_it(self):
        token = 'gh' + 'p_' + 'a' * 36
        first = self.commit('petnote/settings.txt', token)
        self.assertIn('private key or access token', guard.commit_findings(first))
        second = self.commit('petnote/settings.txt', 'safe')
        self.assertEqual(guard.commit_findings(second), set())

    def test_binary_database_filename_is_rejected(self):
        commit = self.commit('petnote/data/session.sqlite3', '')
        self.assertIn('private configuration or database file', guard.commit_findings(commit))

    def test_tree_guard_includes_untracked_files_and_blog(self):
        self.commit('petnote/safe.py', 'value = 1\n')
        Path('petnote/blog').mkdir()
        Path('petnote/blog/private.md').write_text('safe')
        Path('petnote/config.example').write_text('safe')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(guard.check_working_tree(), 0)
        Path('petnote/blog/private.md').write_text('person@' + 'gmail.com')
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(guard.check_working_tree(), 1)
        Path('petnote/.env.production').write_text('value=example')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(guard.check_working_tree(), 1)
        self.assertNotIn('value=example', output.getvalue())


if __name__ == '__main__':
    unittest.main()
