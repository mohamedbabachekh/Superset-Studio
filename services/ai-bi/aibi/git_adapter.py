"""Git, as the engine uses it: two repositories in the service's own volume.

  repos/dbt          a mirror of the governed dbt project on `main`, and one branch per BI project
                     (`ai/<project>`) checked out as its own worktree. The AI writes ONLY there.
  repos/bi-projects  what each BI project is: its specification, its assets, its reports — one commit per
                     version, one tag per promotion.

The governed dbt project itself is mounted read-only into the container. Nothing here can push to it: there
is no remote, and a generated model reaches the real project the way any model does — a person merges it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


class GitError(Exception):
    pass


class GitRepo:
    def __init__(self, path: Path, author: str = "SharpSight AI BI", email: str = "ai-bi@sharpsight.local"):
        self.path = path
        self._env = {
            **os.environ,
            "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": email,
            "GIT_COMMITTER_NAME": author, "GIT_COMMITTER_EMAIL": email,
            "GIT_TERMINAL_PROMPT": "0",
            # no user or system configuration: the result must not depend on whose container this is
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
        }

    def git(self, *args: str, cwd: Optional[Path] = None, check: bool = True) -> str:
        done = subprocess.run(["git", "-c", "safe.directory=*", "-c", "core.autocrlf=false", *args],
                              cwd=str(cwd or self.path), env=self._env, capture_output=True, text=True)
        if check and done.returncode != 0:
            raise GitError(f"git {' '.join(args[:2])}: {done.stderr.strip() or done.stdout.strip()}")
        return done.stdout.strip()

    def ensure(self) -> None:
        if (self.path / ".git").exists():
            return
        self.path.mkdir(parents=True, exist_ok=True)
        self.git("init", "--initial-branch=main")
        self.git("commit", "--allow-empty", "-m", "Repository created by the SharpSight AI BI service")

    def head(self, cwd: Optional[Path] = None) -> str:
        return self.git("rev-parse", "HEAD", cwd=cwd)

    def commit_all(self, message: str, cwd: Optional[Path] = None) -> Optional[str]:
        """Commit everything that changed. Returns the new commit, or None when nothing did."""
        self.git("add", "-A", cwd=cwd)
        if not self.git("status", "--porcelain", cwd=cwd):
            return None
        self.git("commit", "-m", message, cwd=cwd)
        return self.head(cwd=cwd)

    def tag(self, name: str, message: str, ref: str = "HEAD") -> None:
        self.git("tag", "-f", "-a", name, "-m", message, ref)

    def branch_exists(self, name: str) -> bool:
        return bool(self.git("branch", "--list", name))

    def log(self, limit: int = 20, path: Optional[str] = None) -> list[dict[str, str]]:
        args = ["log", f"-{limit}", "--pretty=format:%H%x1f%ad%x1f%s", "--date=iso-strict"]
        if path:
            args += ["--", path]
        out = self.git(*args, check=False)
        return [dict(zip(("commit", "date", "subject"), line.split("\x1f"))) for line in out.splitlines() if line]


_IGNORED = {".git", "target", "logs", "dbt_packages", ".user.yml", "__pycache__"}


class GitAdapter:
    def __init__(self, state_dir: Path):
        self.dbt = GitRepo(state_dir / "repos" / "dbt")
        self.projects = GitRepo(state_dir / "repos" / "bi-projects")
        self.workspaces = state_dir / "workspaces"

    # ── the governed dbt project ────────────────────────────────────────────────────────────────────────────
    def sync_governed_project(self, source: Path) -> str:
        """Mirror the mounted, read-only dbt project onto `main`. Returns the commit `main` now stands on."""
        self.dbt.ensure()
        self.dbt.git("checkout", "-q", "main")
        for entry in self.dbt.path.iterdir():
            if entry.name == ".git":
                continue
            shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        for entry in source.iterdir():
            if entry.name in _IGNORED:
                continue
            target = self.dbt.path / entry.name
            if entry.is_dir():
                shutil.copytree(entry, target, ignore=shutil.ignore_patterns(*_IGNORED))
            else:
                shutil.copy2(entry, target)
        self.dbt.commit_all("Sync the governed dbt project")
        return self.dbt.head()

    def workspace(self, project_id: str) -> Path:
        """The project's isolated dbt workspace: branch `ai/<project>`, brought up to date with `main`."""
        branch = f"ai/{project_id}"
        path = self.workspaces / project_id / "dbt"
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            self.dbt.git("worktree", "prune")
            if self.dbt.branch_exists(branch):
                self.dbt.git("worktree", "add", str(path), branch)
            else:
                self.dbt.git("worktree", "add", "-b", branch, str(path), "main")
        else:
            merged = subprocess.run(
                ["git", "-c", "safe.directory=*", "merge", "--no-edit", "-q", "main"],
                cwd=str(path), env=self.dbt._env, capture_output=True, text=True)
            if merged.returncode != 0:
                self.dbt.git("merge", "--abort", cwd=path, check=False)
                raise GitError(f"branch {branch} no longer merges with the governed project: "
                               f"{merged.stderr.strip() or merged.stdout.strip()}")
        return path

    def commit_workspace(self, project_id: str, message: str) -> str:
        path = self.workspaces / project_id / "dbt"
        self.dbt.commit_all(message, cwd=path)
        return self.dbt.head(cwd=path)

    # ── BI projects ─────────────────────────────────────────────────────────────────────────────────────────
    def project_dir(self, project_id: str) -> Path:
        self.projects.ensure()
        path = self.projects.path / project_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def commit_project(self, project_id: str, message: str) -> str:
        commit = self.projects.commit_all(message)
        return commit or self.projects.head()

    def tag_project(self, project_id: str, version: str, suffix: str, message: str) -> str:
        name = f"{project_id}/v{version}" + (f"-{suffix}" if suffix else "")
        self.projects.tag(name, message)
        return name
