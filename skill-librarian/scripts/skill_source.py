#!/usr/bin/env python3
"""Source acquisition and provenance helpers for skill-librarian imports.

This module deliberately does not write into runtime or canonical skill directories.
It only resolves an external/local source into a temporary/local snapshot, locates
one skill, and produces sanitized provenance metadata that the control-plane CLI
can persist next to the imported skill.

Standard library only. Git-backed acquisition requires the `git` executable.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


PROVENANCE_FILENAME = ".skill-source.json"
PROVENANCE_SCHEMA_VERSION = 1
_DISCOVERY_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", ".venv", "__pycache__"}
_GITHUB_SHORTHAND = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class SourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcquiredSource:
    root: Path
    requested_source: str
    source_type: str
    recorded_source: str | None
    requested_ref: str | None
    revision: str | None
    dirty: bool | None
    acquired_via: str
    path_hint: Path | None = None


def _run_git(args, cwd=None, check=True):
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SourceError("git is required for Git-backed skill imports") from exc

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "git command failed").strip()
        raise SourceError(f"{' '.join(cmd)}: {detail}")
    return proc


def _looks_like_local_path(source):
    expanded = Path(os.path.expanduser(source))
    if expanded.exists():
        return True
    return source.startswith((".", "/", "~")) or (os.name == "nt" and len(source) >= 2 and source[1] == ":")


def sanitize_recorded_source(source):
    """Remove URL credentials/query/fragment before provenance is committed."""
    if not isinstance(source, str) or not source:
        return source

    if source.startswith("git@"):
        # scp-style SSH source. `git` is a public transport username, not a secret.
        return source

    parsed = urlsplit(source)
    if parsed.scheme not in {"http", "https", "ssh", "git"}:
        return source

    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname = f"{hostname}:{parsed.port}"
    username = parsed.username
    if parsed.scheme == "ssh" and username:
        netloc = f"{username}@{hostname}"
    else:
        netloc = hostname
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def _parse_remote_source(source, ref=None):
    """Return (clone_url, effective_ref, optional_path_hint)."""
    if _GITHUB_SHORTHAND.fullmatch(source):
        return f"https://github.com/{source}.git", ref, None

    parsed = urlsplit(source)
    if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.hostname.lower() == "github.com":
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            if repo.endswith(".git"):
                repo = repo[:-4]
            clone_url = f"{parsed.scheme}://github.com/{owner}/{repo}.git"
            if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
                inferred_ref = parts[3]
                if ref is not None and ref != inferred_ref:
                    raise SourceError(
                        f"source URL selects ref '{inferred_ref}' but --ref requested '{ref}'"
                    )
                hint_parts = parts[4:]
                if parts[2] == "blob" and hint_parts and hint_parts[-1] == "SKILL.md":
                    hint_parts = hint_parts[:-1]
                hint = Path(*hint_parts) if hint_parts else None
                return clone_url, inferred_ref, hint
            return clone_url, ref, None

    if parsed.scheme in {"http", "https", "ssh", "git", "file"}:
        return source, ref, None
    if source.startswith("git@") or source.endswith(".git"):
        return source, ref, None

    raise SourceError(
        "Unsupported import source. Use an existing local path, owner/repo, a GitHub URL, or a git URL."
    )


def _git_revision(repo):
    proc = _run_git(["rev-parse", "HEAD"], cwd=repo)
    return proc.stdout.strip()


def _git_dirty(repo):
    proc = _run_git(["status", "--porcelain", "--untracked-files=all"], cwd=repo)
    return bool(proc.stdout.strip())


def _git_root(path):
    proc = _run_git(["rev-parse", "--show-toplevel"], cwd=path, check=False)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return Path(value).resolve() if value else None


def _git_remote(repo):
    proc = _run_git(["config", "--get", "remote.origin.url"], cwd=repo, check=False)
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return sanitize_recorded_source(value) if value else None


def _checkout_ref(repo, ref):
    if not ref:
        return
    attempts = [ref]
    if not ref.startswith("origin/"):
        attempts.append(f"origin/{ref}")
    messages = []
    for candidate in attempts:
        proc = _run_git(["checkout", "--quiet", "--detach", candidate], cwd=repo, check=False)
        if proc.returncode == 0:
            return
        messages.append((proc.stderr or proc.stdout or candidate).strip())
    detail = "; ".join(message for message in messages if message)
    raise SourceError(f"Cannot resolve git ref '{ref}'" + (f": {detail}" if detail else ""))


@contextlib.contextmanager
def acquire_source(source, ref=None):
    """Yield one local or cloned source snapshot without mutating canonical/runtime state."""
    if not isinstance(source, str) or not source.strip():
        raise SourceError("import source must be a non-empty string")
    source = source.strip()

    if _looks_like_local_path(source):
        root = Path(os.path.expanduser(source)).resolve()
        if not root.exists():
            raise SourceError(f"Local import source does not exist: {root}")
        if not root.is_dir():
            raise SourceError(f"Local import source must be a directory: {root}")
        if ref is not None:
            raise SourceError("--ref is only valid for Git-backed remote sources")

        git_root = _git_root(root)
        revision = _git_revision(git_root) if git_root is not None else None
        dirty = _git_dirty(git_root) if git_root is not None else None
        remote = _git_remote(git_root) if git_root is not None else None
        yield AcquiredSource(
            root=root,
            requested_source=source,
            source_type="git" if remote else "local",
            recorded_source=remote,
            requested_ref=None,
            revision=revision,
            dirty=dirty,
            acquired_via="local",
            path_hint=None,
        )
        return

    clone_url, effective_ref, path_hint = _parse_remote_source(source, ref=ref)
    with tempfile.TemporaryDirectory(prefix="skill-librarian-import-") as tmp:
        checkout = Path(tmp) / "source"
        _run_git(["clone", "--quiet", clone_url, str(checkout)])
        _checkout_ref(checkout, effective_ref)
        yield AcquiredSource(
            root=checkout.resolve(),
            requested_source=source,
            source_type="git",
            recorded_source=sanitize_recorded_source(clone_url),
            requested_ref=effective_ref,
            revision=_git_revision(checkout),
            dirty=False,
            acquired_via="git",
            path_hint=path_hint,
        )


def _frontmatter_name(skill_md):
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:80]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"\'')
    return None


def find_skill(source, skill_name):
    """Locate exactly one SKILL.md boundary with matching frontmatter name."""
    root = Path(source.root).resolve()
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise SourceError("--skill must be a non-empty skill name")
    skill_name = skill_name.strip()

    candidates = []

    def consider(directory):
        manifest = directory / "SKILL.md"
        if manifest.is_file() and _frontmatter_name(manifest) == skill_name:
            candidates.append(directory.resolve())
            return True
        return manifest.is_file()

    if source.path_hint is not None:
        hinted = (root / source.path_hint).resolve(strict=False)
        try:
            hinted.relative_to(root)
        except ValueError as exc:
            raise SourceError(f"Source path hint escapes checkout: {source.path_hint}") from exc
        if hinted.is_dir() and consider(hinted):
            return hinted

    if consider(root):
        if candidates:
            return candidates[0]
        # Root is another skill boundary, so discovery must not recurse through it.
        raise SourceError(
            f"Source root is skill '{_frontmatter_name(root / 'SKILL.md')}', not requested '{skill_name}'"
        )

    for current, dirnames, _filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        dirnames[:] = [
            name
            for name in sorted(dirnames)
            if not name.startswith(".") and name not in _DISCOVERY_SKIP_DIRS
        ]

        # root was already considered above.
        if current_path == root:
            continue

        manifest = current_path / "SKILL.md"
        if manifest.is_file():
            if _frontmatter_name(manifest) == skill_name:
                candidates.append(current_path.resolve())
            # Skill boundary: never search below it.
            dirnames[:] = []

    unique = []
    seen = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)

    if not unique:
        raise SourceError(f"Skill '{skill_name}' was not found in source {source.requested_source}")
    if len(unique) > 1:
        choices = ", ".join(str(path.relative_to(root)) for path in unique)
        raise SourceError(
            f"Skill '{skill_name}' is ambiguous in source; matching paths: {choices}"
        )
    return unique[0]


def _local_git_provenance(source, skill_path):
    git_root = _git_root(skill_path)
    if git_root is None:
        try:
            relative = skill_path.resolve().relative_to(source.root.resolve()).as_posix()
        except ValueError:
            relative = skill_path.name
        return {
            "type": "local",
            "source": None,
            "source_path": relative or ".",
            "ref": None,
            "revision": None,
            "dirty": None,
        }

    remote = _git_remote(git_root)
    try:
        relative = skill_path.resolve().relative_to(git_root).as_posix()
    except ValueError:
        relative = skill_path.name
    return {
        "type": "git" if remote else "local",
        "source": remote,
        "source_path": relative or ".",
        "ref": None,
        "revision": _git_revision(git_root),
        "dirty": _git_dirty(git_root),
    }


def build_provenance(source, skill_path, skill_name, imported_at=None):
    skill_path = Path(skill_path).resolve()
    if source.acquired_via == "local":
        origin = _local_git_provenance(source, skill_path)
    else:
        try:
            relative = skill_path.relative_to(source.root.resolve()).as_posix()
        except ValueError as exc:
            raise SourceError(f"Selected skill escapes acquired source: {skill_path}") from exc
        origin = {
            "type": source.source_type,
            "source": source.recorded_source,
            "source_path": relative or ".",
            "ref": source.requested_ref,
            "revision": source.revision,
            "dirty": source.dirty,
        }

    timestamp = imported_at
    if timestamp is None:
        timestamp = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "type": origin["type"],
        "source": origin["source"],
        "source_path": origin["source_path"],
        "ref": origin["ref"],
        "revision": origin["revision"],
        "dirty": origin["dirty"],
        "skill": skill_name,
        "acquired_via": source.acquired_via,
        "imported_at": timestamp,
    }


def write_provenance(skill_dir, metadata):
    path = Path(skill_dir) / PROVENANCE_FILENAME
    path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
