#!/usr/bin/env python3
"""Batch upstream inspection, verified provenance migration, and safe updates.

No runtime installation, project-registry mutation, automatic Git commit/push,
external skill execution, or destructive force-update is performed here.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager, nullcontext
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import uuid


def _sibling(filename, name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


upstream = _sibling("skill_upstream.py", "skill_lifecycle_upstream")
source = upstream.skill_source
importer = _sibling("skill_import.py", "skill_lifecycle_import")
PROVENANCE = source.PROVENANCE_FILENAME
VCS = {".git", ".hg", ".svn"}


class LifecycleError(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ref(value):
    if value is not None and (
        not isinstance(value, str) or not value.strip() or value != value.strip()
        or value.startswith("-") or any(ord(c) < 32 for c in value)
    ):
        raise LifecycleError("Invalid Git ref")
    return value


def _remote(value):
    """Require a portable Git identity; credentials stay in Git's credential store."""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LifecycleError("Source must be a portable Git URL or owner/repo")
    if any(ord(c) < 32 for c in value) or value.startswith("-"):
        raise LifecycleError("Invalid Git source")
    url, inferred, hint = source._parse_remote_source(value)
    if inferred is not None or hint is not None:
        raise LifecycleError("Use a repository-root source plus --ref / --source-path")
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http", "ssh", "git"} and not re.fullmatch(r"git@[^:]+:.+", url):
        raise LifecycleError("Lifecycle tracking requires a portable remote Git source, not a local path")
    if parsed.password or (parsed.username and parsed.scheme != "ssh") or parsed.query or parsed.fragment:
        raise LifecycleError("Do not embed credentials, query strings, or fragments in a Git source")
    return source.sanitize_recorded_source(url)


def _relative(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise LifecycleError("source_path must be a portable relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or any(ord(c) < 32 for c in value):
        raise LifecycleError("source_path must stay inside the upstream checkout")
    return path.as_posix()


def _history_limit(value):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 500:
        raise LifecycleError("history_limit must be an integer between 0 and 500")
    return value


class Snapshots:
    """Per-command cache: shared refs are fetched once, including failures."""
    def __init__(self):
        self.stack = ExitStack()
        self.cache = {}
        self.errors = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def get(self, url, ref=None):
        url, ref = _remote(url), _ref(ref)
        key = (url, ref)
        if key in self.errors:
            raise LifecycleError(self.errors[key])
        if key not in self.cache:
            try:
                item = self.stack.enter_context(source.acquire_source(url, ref=ref))
                if item.acquired_via != "git" or not item.revision or item.dirty:
                    raise LifecycleError("Upstream must be an immutable clean Git checkout")
                self.cache[key] = item
            except Exception as exc:
                self.errors[key] = str(exc)
                raise LifecycleError(str(exc)) from exc
        return self.cache[key]


def _catalog(control):
    skills, clashes = control.discover_skills()
    return skills, {row[0] for row in clashes}


def _canonical(skill, control, *, writing=False):
    control.validate_adopt_skill_name(skill)
    skills, clashes = _catalog(control)
    if skill in clashes:
        raise LifecycleError(f"Ambiguous canonical skill name: {skill}")
    if skill not in skills:
        raise LifecycleError(f"Unknown active canonical skill: {skill}")
    path = Path(skills[skill])
    if not path.is_dir() or path.is_symlink() or control.link_target(path) is not None:
        raise LifecycleError("Canonical skill must be an existing real directory")
    roots = [Path(r).resolve() for r in control.configured_adopt_libraries()]
    if writing and not any(path.resolve().is_relative_to(root) and path.resolve() != root for root in roots):
        raise LifecycleError("Writes require an external configured canonical library; framework skills are read-only")
    return path


def _metadata(path, skill):
    value = upstream.read_provenance(path, expected_skill=skill)
    if value is not None:
        _relative(value["source_path"])
        _ref(value.get("ref"))
        if value.get("type") == "git" and value.get("source"):
            _remote(value["source"])
        revision = value.get("revision")
        if revision is not None and not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", revision):
            raise LifecycleError("Provenance revision must be an exact Git object ID")
    return value


def _locate(acquired, path, skill, *, missing=False):
    path = _relative(path)
    return upstream._locate_provenance_skill(
        acquired, {"source_path": path, "skill": skill}, allow_missing=missing
    )


def _hash(path, skill, control):
    # Validate before hashing, so neither linked files nor linked directories can
    # cause reads outside a skill tree. Hash includes scripts/assets/references.
    control.validate_adopt_skill_tree(path, skill)
    return upstream.fingerprint_skill_tree(path)


def _metadata_bytes(path):
    entry = path / PROVENANCE
    if not os.path.lexists(entry):
        return None
    if entry.is_symlink() or not entry.is_file():
        raise LifecycleError("Provenance must be a regular file")
    return entry.read_bytes()


def _assert_unchanged(path, skill, fingerprint, raw_metadata, control):
    if _hash(path, skill, control) != fingerprint or _metadata_bytes(path) != raw_metadata:
        raise LifecycleError("Canonical content/provenance changed during the operation; retry after review")
    if _canonical(skill, control, writing=True).resolve() != path.resolve():
        raise LifecycleError("Canonical ownership changed during the operation")


def inspect(skill, *, control, snapshots, ref=None):
    path = _canonical(skill, control)
    metadata = _metadata(path, skill)
    result = {
        "skill": skill, "canonical": str(path), "status": "UNTRACKABLE",
        "source": metadata.get("source") if metadata else None,
        "ref": ref if ref is not None else metadata.get("ref") if metadata else None,
        "baseline_revision": metadata.get("revision") if metadata else None,
        "upstream_revision": None, "local_modified": None, "upstream_changed": None,
        "content_matches_upstream": None, "upstream_present": None,
        "provenance_stale": False, "reason": None,
    }
    if not metadata or metadata.get("type") != "git" or not metadata.get("source") or not metadata.get("revision"):
        result["reason"] = "Missing verified Git provenance; run provenance migrate with an explicit source"
        return result
    raw = _metadata_bytes(path)
    local_hash = _hash(path, skill, control)
    baseline = snapshots.get(metadata["source"], metadata["revision"])
    baseline_skill = _locate(baseline, metadata["source_path"], skill)
    baseline_hash = _hash(baseline_skill, skill, control)
    current = snapshots.get(metadata["source"], result["ref"])
    current_skill = _locate(current, metadata["source_path"], skill, missing=True)
    current_hash = _hash(current_skill, skill, control) if current_skill is not None else None
    if _hash(path, skill, control) != local_hash or _metadata_bytes(path) != raw:
        raise LifecycleError("Canonical content/provenance changed while inspecting")
    local_modified = local_hash != baseline_hash
    upstream_changed = current_hash != baseline_hash
    matches = current_hash is not None and local_hash == current_hash
    if matches:
        state = "SAME"
    elif local_modified:
        state = "DIVERGED" if upstream_changed else "LOCAL_MODIFIED"
    else:
        state = "OUTDATED" if upstream_changed else "SAME"
    result.update(
        status=state, baseline_fingerprint=baseline_hash, local_fingerprint=local_hash,
        upstream_fingerprint=current_hash, upstream_revision=current.revision,
        local_modified=local_modified, upstream_changed=upstream_changed,
        content_matches_upstream=matches, upstream_present=current_skill is not None,
        provenance_stale=bool(matches and current.revision != metadata["revision"]),
        reason="Recorded skill path is absent or renamed upstream" if current_skill is None else None,
    )
    return result


@contextmanager
def _lock(path):
    lock = path.parent / (".skill-librarian-upstream-" + path.name + ".lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as exc:
        raise LifecycleError(f"Skill is locked; inspect {lock}/owner.json before removing a stale lock") from exc
    owner = lock / "owner.json"
    try:
        owner.write_text(json.dumps({"pid": os.getpid(), "created_at": _now()}) + "\n", encoding="utf-8")
        yield
    finally:
        if owner.exists():
            owner.unlink()
        lock.rmdir()


def _guard_local_tree(path):
    # The comparison ignores VCS metadata. Never discard it during replacement.
    for _current, directories, files in os.walk(path, followlinks=False):
        if VCS.intersection(directories + files):
            raise LifecycleError("Nested VCS metadata requires manual review before replacing this skill")
    if os.path.lexists(path / ".skill-vendor.json"):
        raise LifecycleError("A vendored project snapshot is not a canonical upstream-update target")


def _validate_incoming(path, skill, control):
    control.validate_adopt_skill_tree(path, skill)
    importer.validate_import_payload(path)
    if os.path.lexists(path / ".skill-vendor.json"):
        raise LifecycleError("Incoming upstream contains unexpected vendor ownership metadata")


def _verify_published(path, skill, expected_hash, expected_metadata, control):
    if _hash(path, skill, control) != expected_hash or _metadata(path, skill) != expected_metadata:
        raise LifecycleError("Published snapshot did not pass final verification")
    if _canonical(skill, control, writing=True).resolve() != path.resolve():
        raise LifecycleError("Updated snapshot is not the active canonical source")


def update(skill, *, control, snapshots, dry_run=False, ref=None, expected_revision=None):
    path = _canonical(skill, control)
    with nullcontext() if dry_run else _lock(path):
        report = inspect(skill, control=control, snapshots=snapshots, ref=ref)
        result = dict(report, action="BLOCKED", dry_run=dry_run)
        if report["status"] not in {"SAME", "OUTDATED"} or not report.get("upstream_present"):
            result["reason"] = report.get("reason") or "Local changes/unknown baseline cannot be overwritten"
            return result
        if expected_revision and report["upstream_revision"] != expected_revision:
            raise LifecycleError("Upstream revision changed since review; --expected-revision does not match")
        old_metadata = _metadata(path, skill)
        if report["status"] == "SAME" and not report["provenance_stale"] and report["ref"] == old_metadata.get("ref"):
            return dict(result, action="UNCHANGED")
        _canonical(skill, control, writing=True)
        _guard_local_tree(path)
        raw_metadata = _metadata_bytes(path)
        acquired = snapshots.get(old_metadata["source"], report["ref"])
        incoming = _locate(acquired, old_metadata["source_path"], skill)
        _validate_incoming(incoming, skill, control)
        metadata = source.build_provenance(acquired, incoming, skill)
        metadata["imported_at"] = old_metadata.get("imported_at", metadata["imported_at"])
        metadata["updated_at"] = _now()
        metadata["previous_revision"] = old_metadata["revision"]
        metadata["baseline_fingerprint"] = report["upstream_fingerprint"]
        if dry_run:
            return dict(result, action="WOULD_UPDATE" if report["status"] == "OUTDATED" else "WOULD_REFRESH")
        stage_root = Path(tempfile.mkdtemp(prefix=".skill-librarian-stage-", dir=path.parent))
        staged = stage_root / skill
        backup = path.parent / (".skill-librarian-backup-" + skill + "-" + uuid.uuid4().hex)
        moved = False
        published = False
        try:
            def ignore(directory, names):
                excluded = VCS | ({PROVENANCE} if Path(directory) == incoming else set())
                return [name for name in names if name in excluded]
            shutil.copytree(incoming, staged, symlinks=True, ignore=ignore)
            _validate_incoming(staged, skill, control)
            if _hash(staged, skill, control) != report["upstream_fingerprint"]:
                raise LifecycleError("Upstream snapshot changed during staging")
            source.write_provenance(staged, metadata)
            _assert_unchanged(path, skill, report["local_fingerprint"], raw_metadata, control)
            os.replace(path, backup)
            moved = True
            os.replace(staged, path)
            published = True
            _verify_published(path, skill, report["upstream_fingerprint"], metadata, control)
        except BaseException as exc:
            if moved:
                try:
                    if published and os.path.lexists(path):
                        os.replace(path, stage_root / "failed-new-snapshot")
                    os.replace(backup, path)
                except BaseException as rollback_exc:
                    # Do not remove either snapshot if rollback cannot complete.
                    raise LifecycleError(f"Rollback needs manual recovery; backup={backup}, stage={stage_root}") from rollback_exc
            shutil.rmtree(stage_root, ignore_errors=True)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise LifecycleError(f"Update failed; original snapshot restored: {exc}") from exc
        shutil.rmtree(stage_root, ignore_errors=True)
        return dict(result, action="UPDATED" if report["status"] == "OUTDATED" else "REFRESHED", backup=str(backup))


def _git(args, cwd=None):
    try:
        proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=120,
                              env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LifecycleError("Local Git history inspection failed or timed out") from exc
    if proc.returncode:
        raise LifecycleError("Local Git history inspection failed: " + (proc.stderr or proc.stdout).strip())
    return proc.stdout.strip()


def _historical_match(acquired, relative, skill, fingerprint, limit, control):
    """Bounded offline history scan in a separate local checkout; no source mutation."""
    revisions = _git([
        "log", "--format=%H", f"--max-count={limit}", acquired.revision, "--", relative
    ], cwd=acquired.root).splitlines()
    with tempfile.TemporaryDirectory(prefix="skill-librarian-history-") as tmp:
        checkout = Path(tmp) / "history"
        _git(["clone", "--quiet", "--shared", "--no-checkout", str(acquired.root), str(checkout)])
        for revision in revisions:
            _git(["checkout", "--quiet", "--detach", revision], cwd=checkout)
            candidate = checkout / relative
            if not candidate.is_dir():
                continue
            try:
                control.ensure_inside_root(candidate, checkout, "History source")
                if _hash(candidate, skill, control) == fingerprint:
                    return revision
            except (LifecycleError, upstream.UpstreamError, control.LibrarianError):
                continue
    return None


def _publish_provenance(path, skill, fingerprint, metadata, control):
    """Publish complete metadata atomically without replacing an existing path."""
    expected = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, filename = tempfile.mkstemp(prefix=".skill-librarian-provenance-", dir=path.parent)
    temporary = Path(filename)
    target = path / PROVENANCE
    linked = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(expected)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_unchanged(path, skill, fingerprint, None, control)
        # Same-filesystem hard link: unlike replace(), this never clobbers a
        # target which appeared concurrently, including a dangling symlink.
        os.link(temporary, target)
        linked = True
        _assert_unchanged(path, skill, fingerprint, expected, control)
    except BaseException:
        # Remove only our unchanged metadata, never a concurrent writer's file.
        if linked and not target.is_symlink() and target.is_file():
            if os.path.samestat(temporary.stat(), target.stat()) and target.read_bytes() == expected:
                target.unlink()
        raise
    finally:
        temporary.unlink()


def migrate(skill, *, remote, control, snapshots, ref=None, source_path=None,
            baseline_ref=None, history_limit=0, dry_run=False):
    path = _canonical(skill, control, writing=True)
    url = _remote(remote)
    _ref(ref)
    _ref(baseline_ref)
    _history_limit(history_limit)
    with nullcontext() if dry_run else _lock(path):
        metadata = _metadata(path, skill)
        if metadata is not None:
            return {"skill": skill, "action": "ALREADY_TRACKED", "dry_run": dry_run,
                    "reason": "Existing provenance is never silently replaced"}
        _guard_local_tree(path)
        fingerprint = _hash(path, skill, control)
        acquired = snapshots.get(url, baseline_ref or ref)
        if source_path is None:
            incoming = source.find_skill(acquired, skill)
            relative = incoming.relative_to(acquired.root).as_posix()
        else:
            relative = _relative(source_path)
            incoming = _locate(acquired, relative, skill)
        revision = acquired.revision if _hash(incoming, skill, control) == fingerprint else None
        if revision is None and history_limit:
            revision = _historical_match(acquired, relative, skill, fingerprint, history_limit, control)
        result = {"skill": skill, "source": url, "source_path": relative, "ref": ref,
                  "revision": revision, "dry_run": dry_run, "action": "NO_MATCH"}
        if revision is None:
            result["reason"] = "No full-tree match; origin/baseline remains unverified and no metadata was written"
            return result
        timestamp = _now()
        metadata = {
            "schema_version": 1, "type": "git", "source": url, "source_path": relative,
            "ref": ref, "revision": revision, "dirty": False, "skill": skill,
            "acquired_via": "migration", "imported_at": timestamp, "migrated_at": timestamp,
            "baseline_fingerprint": fingerprint, "baseline_verified": True,
        }
        if dry_run:
            return dict(result, action="WOULD_MIGRATE")
        _publish_provenance(path, skill, fingerprint, metadata, control)
        return dict(result, action="MIGRATED")


def read_manifest(filename):
    try:
        data = json.loads(Path(filename).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LifecycleError("Cannot read migration manifest") from exc
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("skills"), list):
        raise LifecycleError("Manifest requires schema_version: 1 and a skills array")
    allowed = {"skill", "source", "ref", "source_path", "baseline_ref", "history_limit"}
    seen = set()
    for row in data["skills"]:
        if not isinstance(row, dict) or set(row) - allowed or not isinstance(row.get("skill"), str) or not row.get("source"):
            raise LifecycleError("Invalid migration manifest entry")
        if row["skill"] in seen:
            raise LifecycleError("Duplicate skill in migration manifest")
        seen.add(row["skill"])
        _remote(row["source"])
        _ref(row.get("ref"))
        _ref(row.get("baseline_ref"))
        _history_limit(row.get("history_limit", 0))
        if row.get("source_path") is not None:
            _relative(row["source_path"])
    return data["skills"]


def _emit(results, json_output):
    counts = {}
    for result in results:
        label = result.get("action", result.get("status", "ERROR"))
        counts[label] = counts.get(label, 0) + 1
    if json_output:
        print(json.dumps({"schema_version": 1, "skills": results, "summary": counts}, indent=2, sort_keys=True))
    else:
        print(f"{'SKILL':34} {'STATUS / ACTION':22} SOURCE")
        for row in results:
            label = row.get("action", row.get("status", "ERROR"))
            print(f"{row['skill']:34} {label:22} {row.get('source') or '-'}")
            for key in ("reason", "backup", "upstream_revision"):
                if row.get(key):
                    print(f"  {key}: {row[key]}")
        print("Summary: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))
    if "ERROR" in counts:
        return 2
    return 1 if "BLOCKED" in counts or "NO_MATCH" in counts else 0


def main(argv=None, *, control=None):
    control = control or upstream._load_control_module()
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    p_upstream = subs.add_parser("upstream", help="inspect or safely update canonical skills")
    operations = p_upstream.add_subparsers(dest="operation", required=True)
    for operation in ("status", "update"):
        p = operations.add_parser(operation)
        selection = p.add_mutually_exclusive_group(required=True)
        selection.add_argument("skill", nargs="?")
        selection.add_argument("--all", action="store_true")
        p.add_argument("--json", action="store_true", dest="json_output")
        p.add_argument("--ref", help="explicit tracking-ref override for a single skill")
        if operation == "update":
            p.add_argument("--dry-run", action="store_true")
            p.add_argument("--expected-revision", help="require the exact revision reviewed in status")
    p_provenance = subs.add_parser("provenance", help="attach verified provenance to existing canonical skills")
    migrations = p_provenance.add_subparsers(dest="operation", required=True)
    p_migrate = migrations.add_parser("migrate")
    selection = p_migrate.add_mutually_exclusive_group(required=True)
    selection.add_argument("skill", nargs="?")
    selection.add_argument("--manifest")
    p_migrate.add_argument("--source")
    p_migrate.add_argument("--source-path")
    p_migrate.add_argument("--ref")
    p_migrate.add_argument("--baseline-ref")
    p_migrate.add_argument("--history-limit", type=int, default=0)
    p_migrate.add_argument("--dry-run", action="store_true")
    p_migrate.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        if args.command == "upstream":
            if args.all and (args.ref or getattr(args, "expected_revision", None)):
                parser.error("--ref and --expected-revision require one named skill")
            names = sorted(_catalog(control)[0]) if args.all else [args.skill]
            requests = [(name, {}) for name in names]
        elif args.manifest:
            if args.source or args.source_path or args.ref or args.baseline_ref or args.history_limit:
                parser.error("Use per-entry source/ref settings in --manifest, not single-skill options")
            requests = [(row["skill"], {k: v for k, v in row.items() if k != "skill"}) for row in read_manifest(args.manifest)]
        else:
            if not args.source:
                parser.error("provenance migrate SKILL requires --source")
            requests = [(args.skill, {"source": args.source, "source_path": args.source_path,
                                     "ref": args.ref, "baseline_ref": args.baseline_ref, "history_limit": args.history_limit})]
        results = []
        with Snapshots() as snapshots:
            for name, settings in requests:
                try:
                    if args.command == "provenance":
                        result = migrate(name, remote=settings.pop("source"), control=control,
                                         snapshots=snapshots, dry_run=args.dry_run, **settings)
                    elif args.operation == "status":
                        result = inspect(name, control=control, snapshots=snapshots, ref=args.ref)
                    else:
                        result = update(name, control=control, snapshots=snapshots, dry_run=args.dry_run,
                                        ref=args.ref, expected_revision=args.expected_revision)
                    results.append(result)
                except Exception as exc:
                    results.append({"skill": name, "status": "ERROR", "reason": str(exc)})
        return _emit(results, args.json_output)
    except (LifecycleError, control.LibrarianError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
