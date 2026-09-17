#!/usr/bin/env python3
"""Copy canonical skills into project runtimes as tracked vendored snapshots."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

VENDOR_METADATA_FILENAME = ".skill-vendor.json"
VENDOR_SCHEMA_VERSION = 1
STATUS_VENDORED = "VENDORED"

STATE_SAME = "SAME"
STATE_SOURCE_CHANGED = "SOURCE_CHANGED"
STATE_LOCAL_MODIFIED = "LOCAL_MODIFIED"
STATE_DIVERGED = "DIVERGED"
STATE_SOURCE_MISSING = "SOURCE_MISSING"
STATE_SOURCE_MISSING_LOCAL_MODIFIED = "SOURCE_MISSING_LOCAL_MODIFIED"

_VCS_DIRS = {".git", ".hg", ".svn"}
_IGNORED_FINGERPRINT_FILES = {VENDOR_METADATA_FILENAME, ".skill-source.json"}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class VendorError(RuntimeError):
    pass


def _load_sibling(filename, module_name):
    path = Path(__file__).resolve().with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise VendorError(f"Cannot load {module_name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(module_name, module)
    spec.loader.exec_module(module)
    return module


def _load_control_module():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "discover_skills") and hasattr(main, "runtime_adapters"):
        return main
    return _load_sibling("skill_librarian.py", "skill_librarian_vendor_control")


def _load_inventory_module():
    return _load_sibling("project_inventory.py", "skill_librarian_vendor_inventory")


def _hash_record(hasher, kind, relative, payload=b""):
    hasher.update(kind.encode("ascii"))
    hasher.update(b"\0")
    hasher.update(relative.as_posix().encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(payload)
    hasher.update(b"\0")


def fingerprint_skill_tree(skill_dir):
    """Fingerprint a skill while ignoring generated provenance/vendor metadata."""
    root = Path(skill_dir)
    if not root.is_dir():
        raise VendorError(f"Skill tree is not a directory: {root}")

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
                    raise VendorError(f"Cannot inspect symlink {child}: {exc}") from exc
                _hash_record(hasher, "L", relative, target)
                continue
            _hash_record(hasher, "D", relative)
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        kept_files = [name for name in sorted(filenames) if name not in _IGNORED_FINGERPRINT_FILES]
        for filename in kept_files:
            child = current_path / filename
            relative = child.relative_to(root)
            if child.is_symlink():
                try:
                    target = os.readlink(child).encode("utf-8")
                except OSError as exc:
                    raise VendorError(f"Cannot inspect symlink {child}: {exc}") from exc
                _hash_record(hasher, "L", relative, target)
                continue
            try:
                mode = child.stat().st_mode
            except OSError as exc:
                raise VendorError(f"Cannot stat {child}: {exc}") from exc
            if not stat.S_ISREG(mode):
                raise VendorError(f"Unsupported special file in skill tree: {child}")
            executable = b"1" if mode & 0o111 else b"0"
            try:
                content = child.read_bytes()
            except OSError as exc:
                raise VendorError(f"Cannot read {child}: {exc}") from exc
            _hash_record(hasher, "F", relative, executable + b"\0" + content)

        if relative_current == Path(".") and not dirnames and not kept_files:
            _hash_record(hasher, "D", Path("."))

    return hasher.hexdigest()


def read_vendor_metadata(skill_dir, *, strict=True, expected_skill=None):
    skill_dir = Path(skill_dir)
    marker = skill_dir / VENDOR_METADATA_FILENAME
    if not marker.is_file() or marker.is_symlink():
        if strict:
            raise VendorError(f"Vendored skill metadata is missing: {marker}")
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        if strict:
            raise VendorError(f"Cannot read vendored skill metadata: {marker}")
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != VENDOR_SCHEMA_VERSION:
        if strict:
            raise VendorError(f"Unsupported vendored skill metadata: {marker}")
        return None
    skill = payload.get("skill")
    fingerprint = payload.get("source_fingerprint")
    if not isinstance(skill, str) or not skill.strip() or not isinstance(fingerprint, str) or not _HEX64.match(fingerprint):
        if strict:
            raise VendorError(f"Malformed vendored skill metadata: {marker}")
        return None
    if expected_skill is not None and skill != expected_skill:
        if strict:
            raise VendorError(
                f"Vendored metadata skill is '{skill}', expected '{expected_skill}': {marker}"
            )
        return None
    return payload


def is_vendored_directory(path, expected_skill=None):
    path = Path(path)
    if path.is_symlink() or not path.is_dir():
        return False
    return read_vendor_metadata(path, strict=False, expected_skill=expected_skill) is not None


def annotate_runtime_rows(rows):
    """Turn valid project real-directory snapshots from UNMANAGED into VENDORED."""
    annotated = []
    for row in rows:
        item = dict(row)
        if item.get("status") == "UNMANAGED" and is_vendored_directory(
            item.get("path", ""), expected_skill=item.get("name")
        ):
            item["status"] = STATUS_VENDORED
        annotated.append(item)
    return annotated


def _single_adapter(control, agents):
    adapters = control.runtime_adapters(agents)
    if len(adapters) != 1:
        names = ", ".join(adapter.name for adapter in adapters)
        raise VendorError(f"vendor requires exactly one runtime; selected: {names or '(none)'}")
    return adapters[0]


def _resolve_paths(control, skill, project, agents):
    root = control.git_root(project, required=True)
    adapter = _single_adapter(control, agents)
    target_dir = adapter.project_target(root)
    return root, adapter, target_dir / skill


def _source_identity(control, source):
    source = Path(source).resolve()
    candidates = []
    for root in control.configured_library_roots(include_missing=False):
        root = Path(root).resolve()
        try:
            relative = source.relative_to(root)
        except ValueError:
            continue
        candidates.append((len(root.parts), root, relative))
    if not candidates:
        return None, None
    _depth, root, relative = max(candidates, key=lambda item: item[0])
    return root.name, relative.as_posix()


def _git_snapshot(control, source):
    root = control.git_root(source, required=False)
    if root is None:
        return None, None
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        relative = Path(source).resolve().relative_to(root)
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--", str(relative)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        return revision or None, bool(status.strip())
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None, None


def _metadata(control, source, skill, runtime, fingerprint):
    library, source_path = _source_identity(control, source)
    revision, dirty = _git_snapshot(control, source)
    return {
        "schema_version": VENDOR_SCHEMA_VERSION,
        "skill": skill,
        "runtime": runtime,
        "source_library": library,
        "source_path": source_path,
        "source_revision": revision,
        "source_dirty": dirty,
        "source_fingerprint": fingerprint,
        "vendored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _copy_ignore(_directory, names):
    ignored = set(names) & _VCS_DIRS
    if VENDOR_METADATA_FILENAME in names:
        ignored.add(VENDOR_METADATA_FILENAME)
    return ignored


def _write_metadata(destination, metadata):
    marker = Path(destination) / VENDOR_METADATA_FILENAME
    marker.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _stage_snapshot(control, source, skill, runtime, target_dir):
    source_fingerprint = fingerprint_skill_tree(source)
    stage_root = Path(tempfile.mkdtemp(prefix=f".skill-librarian-vendor-{skill}-", dir=str(target_dir)))
    staged = stage_root / skill
    try:
        shutil.copytree(source, staged, symlinks=True, ignore=_copy_ignore)
        metadata = _metadata(control, source, skill, runtime, source_fingerprint)
        _write_metadata(staged, metadata)
        copied_fingerprint = fingerprint_skill_tree(staged)
        if copied_fingerprint != source_fingerprint:
            raise VendorError(
                f"Vendored snapshot verification failed for '{skill}': copied tree differs from canonical source"
            )
        return stage_root, staged, metadata
    except Exception:
        shutil.rmtree(stage_root, ignore_errors=True)
        raise


def _inspect(control, skill, project, agents):
    root, adapter, target = _resolve_paths(control, skill, project, agents)
    if not os.path.lexists(target):
        raise VendorError(f"Vendored skill does not exist: {control.short(target)}")
    if target.is_symlink() or not target.is_dir():
        raise VendorError(f"Vendor target is not a real directory: {control.short(target)}")

    metadata = read_vendor_metadata(target, strict=True, expected_skill=skill)
    baseline = metadata["source_fingerprint"]
    local_fingerprint = fingerprint_skill_tree(target)
    local_modified = local_fingerprint != baseline

    skills, _ = control.discover_skills()
    source = skills.get(skill)
    if source is None:
        state = STATE_SOURCE_MISSING_LOCAL_MODIFIED if local_modified else STATE_SOURCE_MISSING
        current_fingerprint = None
    else:
        current_fingerprint = fingerprint_skill_tree(source)
        source_changed = current_fingerprint != baseline
        if not local_modified and not source_changed:
            state = STATE_SAME
        elif not local_modified and source_changed:
            state = STATE_SOURCE_CHANGED
        elif local_modified and not source_changed:
            state = STATE_LOCAL_MODIFIED
        else:
            state = STATE_DIVERGED

    return {
        "skill": skill,
        "status": STATUS_VENDORED,
        "state": state,
        "runtime": adapter.name,
        "project": str(root),
        "path": str(target),
        "canonical": str(source) if source is not None else None,
        "baseline_fingerprint": baseline,
        "local_fingerprint": local_fingerprint,
        "current_fingerprint": current_fingerprint,
        "local_modified": local_modified,
        "metadata": metadata,
    }


def vendor_create(skill, project, *, agents=None, dry_run=False, control=None, register_project=None):
    control = control or _load_control_module()
    root, adapter, target = _resolve_paths(control, skill, project, agents)
    skills, _ = control.discover_skills()
    source = skills.get(skill)
    if source is None:
        available = ", ".join(sorted(skills)) or "(none)"
        raise VendorError(f"Unknown skill '{skill}'. Available: {available}")
    if os.path.lexists(target):
        raise VendorError(
            f"Refusing to replace existing runtime entry {control.short(target)}; unmount/remove it first"
        )

    if dry_run:
        print(f"VENDOR   {skill} [{adapter.name}:project] {control.short(target)} <- {control.short(source)}")
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    stage_root, staged, _metadata_payload = _stage_snapshot(
        control, source, skill, adapter.name, target.parent
    )
    try:
        os.replace(staged, target)
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    print(f"VENDORED {skill} [{adapter.name}:project] {control.short(target)} <- {control.short(source)}")
    registrar = register_project or _load_inventory_module().add_project
    registrar(str(root))
    return 0


def vendor_status(skill, project, *, agents=None, json_output=False, control=None):
    control = control or _load_control_module()
    result = _inspect(control, skill, project, agents)
    if json_output:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print(f"VENDORED {skill} [{result['runtime']}:project]")
    print(f"STATE    {result['state']}")
    print(f"PROJECT  {control.short(result['project'])}")
    print(f"PATH     {control.short(result['path'])}")
    print(f"SOURCE   {control.short(result['canonical']) if result['canonical'] else '(missing)'}")
    print(f"BASELINE {result['baseline_fingerprint']}")
    print(f"LOCAL    {result['local_fingerprint']}")
    print(f"CURRENT  {result['current_fingerprint'] or '(missing)'}")
    return 0


def vendor_update(skill, project, *, agents=None, force=False, dry_run=False, control=None):
    control = control or _load_control_module()
    result = _inspect(control, skill, project, agents)
    if result["canonical"] is None:
        raise VendorError(f"Canonical skill '{skill}' is missing; cannot update vendored snapshot")
    if result["local_modified"] and not force:
        raise VendorError(
            f"Vendored skill '{skill}' has local modifications ({result['state']}); use --force to replace them"
        )
    if result["state"] == STATE_SAME:
        print(f"OK       {skill} vendored snapshot already matches canonical source")
        return 0

    source = Path(result["canonical"])
    target = Path(result["path"])
    adapter_name = result["runtime"]
    if dry_run:
        print(f"UPDATE   {skill} [{adapter_name}:project] {control.short(target)} <- {control.short(source)}")
        return 0

    stage_root, staged, _metadata_payload = _stage_snapshot(
        control, source, skill, adapter_name, target.parent
    )
    backup = target.parent / f".skill-librarian-vendor-backup-{skill}-{uuid.uuid4().hex}"
    moved = False
    try:
        os.replace(target, backup)
        moved = True
        os.replace(staged, target)
        final = _inspect(control, skill, result["project"], [adapter_name])
        if final["state"] != STATE_SAME:
            raise VendorError(f"Vendor update verification failed: state is {final['state']}")
    except Exception:
        if os.path.lexists(target):
            shutil.rmtree(target, ignore_errors=True)
        if moved and os.path.lexists(backup):
            os.replace(backup, target)
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)

    shutil.rmtree(backup, ignore_errors=True)
    print(f"UPDATED  {skill} [{adapter_name}:project] {control.short(target)}")
    return 0


def vendor_remove(skill, project, *, agents=None, force=False, dry_run=False, control=None):
    control = control or _load_control_module()
    result = _inspect(control, skill, project, agents)
    if result["local_modified"] and not force:
        raise VendorError(
            f"Vendored skill '{skill}' has local modifications ({result['state']}); use --force to remove it"
        )
    target = Path(result["path"])
    if dry_run:
        print(f"REMOVE   {skill} [{result['runtime']}:project] {control.short(target)}")
        return 0
    shutil.rmtree(target)
    print(f"REMOVED  {skill} [{result['runtime']}:project] {control.short(target)}")
    return 0


def _add_common(parser):
    parser.add_argument("skill")
    parser.add_argument("--project", required=True, metavar="REPO")
    parser.add_argument(
        "-a",
        "--agent",
        dest="agents",
        action="append",
        metavar="RUNTIME",
        help="runtime to vendor into; exactly one runtime is required",
    )


def main(argv=None, *, control=None, register_project=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    control = control or _load_control_module()

    mode = "create"
    if argv and argv[0] in {"status", "update", "remove"}:
        mode = argv.pop(0)

    parser = argparse.ArgumentParser(description="Vendor canonical skills into project runtime directories.")
    _add_common(parser)
    if mode == "status":
        parser.add_argument("--json", action="store_true", dest="json_output")
    elif mode in {"update", "remove"}:
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
    else:
        parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args(argv)
    try:
        if mode == "create":
            return vendor_create(
                args.skill,
                args.project,
                agents=args.agents,
                dry_run=args.dry_run,
                control=control,
                register_project=register_project,
            )
        if mode == "status":
            return vendor_status(
                args.skill,
                args.project,
                agents=args.agents,
                json_output=args.json_output,
                control=control,
            )
        if mode == "update":
            return vendor_update(
                args.skill,
                args.project,
                agents=args.agents,
                force=args.force,
                dry_run=args.dry_run,
                control=control,
            )
        return vendor_remove(
            args.skill,
            args.project,
            agents=args.agents,
            force=args.force,
            dry_run=args.dry_run,
            control=control,
        )
    except (VendorError, control.LibrarianError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
