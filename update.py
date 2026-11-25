"""Bring this folder up to date from GitHub.

Run by the .bat files before they do anything else. It never stands in the
way: offline, signed out, git missing, a file edited by hand -- any of those
prints one line and leaves the current version exactly as it was.

The first time it runs in a folder that came from a zip, it turns the folder
into a clone of the repo in place. Only files the repo tracks are replaced;
everything else (the database, publish.json, the site, the attempt log) is
ignored by the repo and never touched.

A file named `no-updates` in the folder switches this off -- for the copy the
updates are made in.
"""

import os
import shutil
import stat
import subprocess
import sys

REMOTE = "https://github.com/JNicholson91/hitless-recorder.git"
BRANCH = "main"
HERE = os.path.dirname(os.path.abspath(__file__))
TIMEOUT = 60


def git(*args, cwd=HERE):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")   # fail, never wait for a password
    try:
        done = subprocess.run(["git", *args], cwd=cwd, env=env, text=True,
                              capture_output=True, timeout=TIMEOUT)
    except FileNotFoundError:
        return 127, "", "git not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timed out"
    return done.returncode, done.stdout.strip(), done.stderr.strip()


def _remove_tree(path):
    """rmtree that also clears read-only files (git's objects are read-only on
    Windows, and a half-removed .git would look like a repo with no commits)."""
    def fix(func, p, *_):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=fix)
    else:
        shutil.rmtree(path, onerror=fix)


def say(msg):
    print("  " + msg, flush=True)


def adopt(remote=REMOTE, branch=BRANCH, cwd=HERE):
    """Make a zip-installed folder a clone of `remote`, in place."""
    git_dir = os.path.join(cwd, ".git")
    code, _, _ = git("init", "-q", cwd=cwd)
    if code != 0:
        return False
    steps = [("remote", "add", "origin", remote),
             ("fetch", "-q", "origin", branch)]
    for step in steps:
        if git(*step, cwd=cwd)[0] != 0:
            _remove_tree(git_dir)   # back to how it was
            return False
    for step in [("symbolic-ref", "HEAD", "refs/heads/%s" % branch),
                 ("reset", "-q", "origin/%s" % branch),       # index only: files untouched
                 ("checkout", "-q", "--", "."),              # tracked files -> repo version
                 ("branch", "-q", "--set-upstream-to=origin/%s" % branch, branch)]:
        if git(*step, cwd=cwd)[0] != 0:
            return False
    return True


def update(remote=REMOTE, branch=BRANCH, cwd=HERE):
    """Returns a short status word; prints what happened."""
    if os.path.exists(os.path.join(cwd, "no-updates")):
        return "off"
    if git("--version", cwd=cwd)[0] != 0:
        say("[!] git is not installed, so updates are off. Everything else works.")
        return "no-git"

    git_dir = os.path.join(cwd, ".git")
    if os.path.isdir(git_dir) and git("rev-parse", "--verify", "-q", "HEAD", cwd=cwd)[0] != 0:
        _remove_tree(git_dir)        # a first attempt that never finished: start again
    if not os.path.isdir(git_dir):
        if adopt(remote, branch, cwd):
            _, head, _ = git("log", "-1", "--format=%s", cwd=cwd)
            say("[ok] automatic updates switched on (latest: %s)" % head)
            return "adopted"
        say("[!] could not reach the update server; using this version.")
        return "offline"

    if git("fetch", "-q", "origin", branch, cwd=cwd)[0] != 0:
        say("[!] could not check for updates (offline?); using this version.")
        return "offline"
    _, behind, _ = git("rev-list", "--count", "HEAD..origin/%s" % branch, cwd=cwd)
    if behind in ("", "0"):
        say("[ok] up to date")
        return "current"

    _, changes, _ = git("log", "--format=%s", "HEAD..origin/%s" % branch, cwd=cwd)
    if git("merge", "-q", "--ff-only", "origin/%s" % branch, cwd=cwd)[0] != 0:
        _, edited, _ = git("status", "--porcelain", "--untracked-files=no", cwd=cwd)
        say("[!] an update is waiting but could not be applied%s." % (
            " because these files were changed here: %s"
            % ", ".join(line[3:] for line in edited.splitlines()[:4]) if edited else ""))
        say("    Using this version. Tell John.")
        return "stuck"
    say("[ok] updated:")
    for line in changes.splitlines()[:8]:
        say("     - " + line)
    return "updated"


if __name__ == "__main__":
    try:
        update()
    except Exception as exc:                 # an updater must never stop the run
        say("[!] update check failed (%s); using this version." % exc)
    sys.exit(0)
