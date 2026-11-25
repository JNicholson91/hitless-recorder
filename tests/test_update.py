"""The self-updater, against real git repos on disk (no network)."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("update", os.path.join(ROOT, "update.py"))
update = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(update)

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def sh(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, env=ENV, check=True,
                   capture_output=True, text=True)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@unittest.skipUnless(shutil.which("git"), "git not installed")
class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.remote = os.path.join(self.dir, "remote.git")
        sh("init", "-q", "--bare", "--initial-branch=main", self.remote, cwd=self.dir)
        self.dev = os.path.join(self.dir, "dev")            # where John works
        os.makedirs(self.dev)
        sh("init", "-q", "--initial-branch=main", cwd=self.dev)
        write(os.path.join(self.dev, ".gitignore"), "/*\n!/.gitignore\n!/code.py\n!/run.bat\n")
        write(os.path.join(self.dev, "code.py"), "VERSION = 1\n")
        write(os.path.join(self.dev, "run.bat"), "@echo off\r\n")
        self.release("v1")
        # Alice's folder: unzipped v1 plus her own files the repo must never touch.
        self.her = os.path.join(self.dir, "her")
        os.makedirs(self.her)
        write(os.path.join(self.her, "code.py"), "VERSION = 1\n")
        write(os.path.join(self.her, "run.bat"), "@echo off\r\n")
        write(os.path.join(self.her, "publish.json"), '{"mine": true}\n')
        write(os.path.join(self.her, "data", "attempts.jsonl"), '{"a": 1}\n')

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def release(self, msg):
        sh("add", "-A", cwd=self.dev)
        sh("commit", "-q", "-m", msg, cwd=self.dev)
        if not os.path.isdir(os.path.join(self.dev, ".git", "refs", "remotes", "origin")):
            sh("remote", "add", "origin", self.remote, cwd=self.dev)
        sh("push", "-q", "origin", "main", cwd=self.dev)

    def run_update(self):
        return update.update(self.remote, "main", self.her)

    def test_first_run_adopts_the_zip_folder_and_keeps_her_files(self):
        write(os.path.join(self.dev, "code.py"), "VERSION = 2\n")
        self.release("v2")
        self.assertEqual(self.run_update(), "adopted")
        self.assertEqual(read(os.path.join(self.her, "code.py")), "VERSION = 2\n")
        self.assertEqual(read(os.path.join(self.her, "publish.json")), '{"mine": true}\n')
        self.assertTrue(os.path.exists(os.path.join(self.her, "data", "attempts.jsonl")))

    def test_then_up_to_date_then_updated(self):
        self.run_update()
        self.assertEqual(self.run_update(), "current")
        write(os.path.join(self.dev, "code.py"), "VERSION = 3\n")
        self.release("v3: something new")
        self.assertEqual(self.run_update(), "updated")
        self.assertEqual(read(os.path.join(self.her, "code.py")), "VERSION = 3\n")
        self.assertEqual(read(os.path.join(self.her, "publish.json")), '{"mine": true}\n')

    def test_a_hand_edited_file_is_never_overwritten(self):
        self.run_update()
        write(os.path.join(self.her, "code.py"), "VERSION = 'hers'\n")
        write(os.path.join(self.dev, "code.py"), "VERSION = 4\n")
        self.release("v4")
        self.assertEqual(self.run_update(), "stuck")
        self.assertEqual(read(os.path.join(self.her, "code.py")), "VERSION = 'hers'\n")

    def test_unreachable_remote_leaves_the_folder_as_it_was(self):
        status = update.update(os.path.join(self.dir, "nope.git"), "main", self.her)
        self.assertEqual(status, "offline")
        self.assertFalse(os.path.exists(os.path.join(self.her, ".git")))
        self.assertEqual(read(os.path.join(self.her, "code.py")), "VERSION = 1\n")

    def test_no_updates_marker(self):
        write(os.path.join(self.her, "no-updates"), "")
        self.assertEqual(self.run_update(), "off")
        self.assertFalse(os.path.exists(os.path.join(self.her, ".git")))

    def test_a_git_folder_with_no_commits_is_redone(self):
        sh("init", "-q", cwd=self.her)                   # .git exists, nothing in it
        self.assertEqual(self.run_update(), "adopted")
        self.assertEqual(self.run_update(), "current")

    def test_bat_files_keep_crlf(self):
        self.run_update()
        with open(os.path.join(self.her, "run.bat"), "rb") as fh:
            self.assertIn(b"\r\n", fh.read())


if __name__ == "__main__":
    unittest.main()
