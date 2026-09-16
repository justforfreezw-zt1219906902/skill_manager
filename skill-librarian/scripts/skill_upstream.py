#!/usr/bin/env python3
"""Read-only upstream status engine for canonical skills.

This module is the first v0.6 lifecycle layer. It reads `.skill-source.json`,
reconstructs the imported baseline, fetches the current upstream snapshot, and
classifies canonical skill state without mutating the canonical library or any
runtime directory.

The write path (`update`) is intentionally not implemented here yet.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys
from pathlib import Path


STATUS_SAME = "SAME"
STATUS_OUTDATED = "OUTDATED"
STATUS_LOCAL_MODIFIED = "LOCAL_MODIFIED"
STATUS_DIVERGED = "DIVERGED"
STATUS_UNTRACKABLE = "UNTRACKABLE"

_VCS_DIRS = {".git", ".hg", ".svn"}


class UpstreamError(RuntimeError):
    pass


def _load_sibling_module(filename, module_name):
    module_path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise UpstreamError(f"Cannot load {module_name} from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, module)
    spec.loader.exec_module(module)
    return module


def _load_skill_source_module():
    try:
        import skill_source as module
        return module
    except ModuleNotFoundError:
        return _load_sibling_module("skill_source.py", "skill_source")


def _load_control_module():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "discover_skills") and hasattr(main, "validate_adopt_skill_name"):
        return main
    return _load_sibling_module("skill_librarian.py", "skill_librarian_control")


skill_source = _load_skill_source_module()


def _validate_relative_source_path(raw):
    if not isinstance(raw, str) or not raw.strip():
        raise UpstreamError("provenance source_path must be a non-empty relative path")
    candidate = Path(raw.strip())
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise UpstreamError("provenance source_path must stay inside the upstream checkout")
    return candidate


def read_provenance(skill_dir, expected_skill=None):
    """Read and validate provenance schema v1.

    Missing provenance is a normal `UNTRACKABLE` condition and returns None.
    Existing but malformed provenance is an error because silently ignoring a
    corrupt ownership record would make lifecycle decisions unsafe.
    """
    skill_dir = Path(skill_dir)
    path = skill_dir / skill_source.PROVENANCE_FILENAME
    if not os.path.lexists(path):
        return None
    if path.is_symlink() or not path.is_file():
        raise UpstreamError(f"Provenance must be a regular file: {path}")

    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpstreamError(f"Cannot read provenance {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise UpstreamError(f"Provenance must be a JSON object: {path}")
    if metadata.get("schema_version") != skill_source.PROVENANCE_SCHEMA_VERSION:
        raise UpstreamError(
            f"Unsupported provenance schema_version {metadata.get('schema_version')!r}; "
            f"expected {skill_source.PROVENANCE_SCHEMA_VERSION}"
        )

    skill = metadata.get("skill")
    if not isinstance(skill, str) or not skill.strip():
        raise UpstreamError("provenance skill must be a non-empty string")
    if expected_skill is not None and skill != expected_skill:
        raise UpstreamError(
            f"Provenance skill is '{skill}', but canonical directory is '{expected_skill}'"
        )

    source_type = metadata.get("type")
    if source_type not in {"git", "local"}:
        raise UpstreamError(f"Unsupported provenance type: {source_type!r}")

    source = metadata.get("source")
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise UpstreamError("provenance source must be null or a non-empty string")

    revision = metadata.get("revision")
    if revision is not None and (not isinstance(revision, str) or not revision.strip()):
        raise UpstreamError("provenance revision must be null or a non-empty string")

    ref = metadata.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip()):
        raise UpstreamError("provenance ref must be null or a non-empty string")

    _validate_relative_source_path(metadata.get("source_path"))
    return metadata


def _hash_record(hasher, kind, relative, payload=b""):
    hasher.update(kind.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(relative.as_posix().encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")


def fingerprint_skill_tree(skill_dir):
    """Return a deterministic content fingerprint for one canonical skill tree.

    Generated provenance and nested VCS metadata are intentionally excluded.
    File contents, relative paths, symlink targets, empty directories, and the
    executable bit are included so meaningful local edits are detected.
    """
    root = Path(skill_dir)
    if not root.is_dir():
        raise UpstreamError(f"Skill tree is not a directory: {root}")

    hasher = hashlib.sha256()
    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_current = current_path.relative_to(root)

        kept_dirs = []
        for dirname in sorted(dirnames):
            child = current_path / dirname
            relative = child.relative_to(root)
            if dirname in _VCS_DIRS:
                continue
            if child.is_symlink():
                try:
                    target = os.readlink(child).encode("utf-8")
                except OSError as exc:
                    raise UpstreamError(f"Cannot inspect symlink {child}: {exc}") from exc
                _hash_record(hasher, "L", relative, target)
                continue
            _hash_record(hasher, "D", relative)
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in sorted(filenames):
            relative = (current_path / filename).relative_to(root)
            if relative == Path(skill_source.PROVENANCE_FILENAME):
                continue
            child = current_path / filename
            if child.is_symlink():
                try:
                    target = os.readlink(child).encode("utf-8")
                except OSError as exc:
                    raise UpstreamError(f"Cannot inspect symlink {child}: {exc}") from exc
                _hash_record(hasher, "L", relative, target)
                continue
            try:
                mode = child.stat().st_mode
            except OSError as exc:
                raise UpstreamError(f"Cannot stat {child}: {exc}") from exc
            if not stat.S_ISREG(mode):
                raise UpstreamError(f"Unsupported special file in skill tree: {child}")
            executable = b"1" if mode & 0o111 else b"0"
            try:
                content = child.read_bytes()
            except OSError as exc:
                raise UpstreamError(f"Cannot read {child}: {exc}") from exc
            _hash_record(hasher, "F", relative, executable + b"\0" + content)

        if relative_current == Path(".") and not dirnames and not filenames:
            _hash_record(hasher, "D", Path("."))

    return hasher.hexdigest()


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


def _locate_provenance_skill(acquired, metadata, *, allow_missing=False):
    relative = _validate_relative_source_path(metadata["source_path"])
    root = Path(acquired.root).resolve()
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise UpstreamError(
            f"Provenance source_path escapes acquired source: {metadata['source_path']}"
        ) from exc

    if not candidate.is_dir():
        if allow_missing:
            return None
        raise UpstreamError(
            f"Imported baseline no longer contains source_path {metadata['source_path']}"
        )

    manifest = candidate / "SKILL.md"
    if not manifest.is_file():
        if allow_missing:
            return None
        raise UpstreamError(f"Upstream skill has no SKILL.md at {candidate}")
    found_name = _frontmatter_name(manifest)
    if found_name != metadata["skill"]:
        if allow_missing:
            return None
        raise UpstreamError(
            f"Upstream source_path is skill '{found_name}', expected '{metadata['skill']}'"
        )
    return candidate


def _untrackable_result(skill, canonical, metadata, reason):
    return {
        "skill": skill,
        "status": STATUS_UNTRACKABLE,
        "canonical": str(canonical),
        "source": metadata.get("source") if metadata else None,
        "ref": metadata.get("ref") if metadata else None,
        "baseline_revision": metadata.get("revision") if metadata else None,
        "upstream_revision": None,
        "local_modified": None,
        "upstream_changed": None,
        "content_matches_upstream": None,
        "upstream_present": None,
        "provenance_stale": False,
        "reason": reason,
    }


def inspect_upstream_status(canonical_skill, metadata):
    """Compare canonical content with imported baseline and current upstream."""
    canonical_skill = Path(canonical_skill).resolve()
    skill = metadata["skill"]

    if metadata.get("type") != "git":
        return _untrackable_result(
            skill,
            canonical_skill,
            metadata,
            "provenance is local-only and has no durable Git upstream",
        )
    source = metadata.get("source")
    revision = metadata.get("revision")
    if not source or not revision:
        return _untrackable_result(
            skill,
            canonical_skill,
            metadata,
            "provenance does not contain both Git source and exact imported revision",
        )

    local_hash = fingerprint_skill_tree(canonical_skill)

    try:
        with skill_source.acquire_source(source, ref=revision) as baseline_acquired:
            baseline_skill = _locate_provenance_skill(baseline_acquired, metadata)
            baseline_hash = fingerprint_skill_tree(baseline_skill)

        with skill_source.acquire_source(source, ref=metadata.get("ref")) as current_acquired:
            current_skill = _locate_provenance_skill(
                current_acquired,
                metadata,
                allow_missing=True,
            )
            upstream_revision = current_acquired.revision
            upstream_hash = (
                fingerprint_skill_tree(current_skill) if current_skill is not None else None
            )
    except skill_source.SourceError as exc:
        raise UpstreamError(f"Cannot inspect upstream for '{skill}': {exc}") from exc

    local_modified = local_hash != baseline_hash
    upstream_present = upstream_hash is not None
    upstream_changed = not upstream_present or upstream_hash != baseline_hash
    content_matches_upstream = upstream_present and local_hash == upstream_hash
    provenance_stale = bool(
        content_matches_upstream
        and upstream_changed
        and upstream_revision
        and upstream_revision != revision
    )

    if content_matches_upstream:
        status = STATUS_SAME
    elif not local_modified and not upstream_changed:
        status = STATUS_SAME
    elif not local_modified and upstream_changed:
        status = STATUS_OUTDATED
    elif local_modified and not upstream_changed:
        status = STATUS_LOCAL_MODIFIED
    else:
        status = STATUS_DIVERGED

    reason = None
    if provenance_stale:
        reason = "canonical content already matches current upstream; provenance baseline is stale"
    elif not upstream_present:
        reason = "recorded upstream source_path is absent at current upstream revision"

    return {
        "skill": skill,
        "status": status,
        "canonical": str(canonical_skill),
        "source": source,
        "ref": metadata.get("ref"),
        "baseline_revision": revision,
        "upstream_revision": upstream_revision,
        "local_modified": local_modified,
        "upstream_changed": upstream_changed,
        "content_matches_upstream": content_matches_upstream,
        "upstream_present": upstream_present,
        "provenance_stale": provenance_stale,
        "reason": reason,
    }


def status_skill(skill, *, control=None):
    control = control or _load_control_module()
    try:
        control.validate_adopt_skill_name(skill)
    except Exception as exc:
        raise UpstreamError(str(exc)) from exc

    skills, _ = control.discover_skills()
    canonical = skills.get(skill)
    if canonical is None:
        available = ", ".join(sorted(skills)) or "(none)"
        raise UpstreamError(f"Unknown canonical skill '{skill}'. Available: {available}")

    metadata = read_provenance(canonical, expected_skill=skill)
    if metadata is None:
        return _untrackable_result(
            skill,
            canonical,
            None,
            f"{skill_source.PROVENANCE_FILENAME} is missing",
        )
    return inspect_upstream_status(canonical, metadata)


def print_status(result, *, json_output=False):
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return

    print(f"{result['status']:16} {result['skill']}")
    print(f"  canonical: {result['canonical']}")
    if result.get("source"):
        print(f"  source:    {result['source']}")
    if result.get("ref"):
        print(f"  ref:       {result['ref']}")
    if result.get("baseline_revision"):
        print(f"  imported:  {result['baseline_revision']}")
    if result.get("upstream_revision"):
        print(f"  upstream:  {result['upstream_revision']}")
    if result.get("local_modified") is not None:
        print(f"  local modified:    {str(result['local_modified']).lower()}")
        print(f"  upstream changed:  {str(result['upstream_changed']).lower()}")
    if result.get("reason"):
        print(f"  note:      {result['reason']}")


def build_parser():
    parser = argparse.ArgumentParser(description="Inspect canonical skill upstream status (read-only).")
    subs = parser.add_subparsers(dest="command", required=True)
    p_status = subs.add_parser("status", help="compare one canonical skill with its imported and current upstream")
    p_status.add_argument("skill")
    p_status.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv=None, control=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "status":
            result = status_skill(args.skill, control=control)
            print_status(result, json_output=args.json_output)
            return 0
        raise UpstreamError(f"Unsupported upstream command: {args.command}")
    except UpstreamError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
