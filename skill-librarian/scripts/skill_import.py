#!/usr/bin/env python3
"""Import external skills into a configured canonical library with provenance.

This is the v0.5 acquisition workflow. It intentionally does not install or mount
skills into agent runtimes. Acquisition, canonical ownership, and runtime
activation remain separate operations.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tempfile
from pathlib import Path


def _load_skill_source_module():
    try:
        import skill_source as module
        return module
    except ModuleNotFoundError:
        module_path = Path(__file__).resolve().with_name("skill_source.py")
        spec = importlib.util.spec_from_file_location("skill_source", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("skill_source", module)
        spec.loader.exec_module(module)
        return module


skill_source = _load_skill_source_module()

DEFAULT_IMPORT_CATEGORY = "imported"
_TRANSIENT_DIRS = {".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache"}
_VCS_DIRS = {".git", ".hg", ".svn"}
_SAFE_ENV_SUFFIXES = {".example", ".sample", ".template"}


class ImportWorkflowError(RuntimeError):
    pass


def _load_control_module():
    main = sys.modules.get("__main__")
    if main is not None and hasattr(main, "discover_skills") and hasattr(main, "resolve_adopt_library"):
        return main

    module_path = Path(__file__).resolve().with_name("skill_librarian.py")
    spec = importlib.util.spec_from_file_location("skill_librarian_control", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_sensitive_env_file(name):
    if name == ".env":
        return True
    if not name.startswith(".env."):
        return False
    suffix = name[len(".env"):]
    return suffix not in _SAFE_ENV_SUFFIXES


def validate_import_payload(source_skill):
    """Reject obvious machine-local/cache/secret payloads before canonicalization."""
    source_skill = Path(source_skill)
    for current, dirnames, filenames in os.walk(source_skill, followlinks=False):
        current_path = Path(current)
        for dirname in list(dirnames):
            if dirname in _TRANSIENT_DIRS:
                raise ImportWorkflowError(
                    f"Import source contains transient dependency/cache directory: {current_path / dirname}"
                )
        for filename in filenames:
            if _is_sensitive_env_file(filename):
                raise ImportWorkflowError(
                    f"Import source contains environment/secrets file: {current_path / filename}. "
                    "Move secrets to machine-local config before importing."
                )


def _copy_ignore(_directory, names):
    return [name for name in names if name in _VCS_DIRS]


def _format_provenance(metadata):
    source = metadata.get("source") or "local snapshot"
    revision = metadata.get("revision") or "unversioned"
    path = metadata.get("source_path") or "."
    dirty = metadata.get("dirty")
    dirty_text = " dirty" if dirty else ""
    return f"{metadata.get('type')} {source} @ {revision}{dirty_text} ({path})"


def import_skill(
    source,
    skill,
    *,
    library=None,
    category=DEFAULT_IMPORT_CATEGORY,
    ref=None,
    dry_run=False,
    control=None,
):
    control = control or _load_control_module()

    try:
        control.validate_adopt_skill_name(skill)
    except Exception as exc:
        raise ImportWorkflowError(str(exc)) from exc

    existing, _ = control.discover_skills()
    if skill in existing:
        raise ImportWorkflowError(
            f"Canonical skill '{skill}' already exists at {control.short(existing[skill])}"
        )

    try:
        library_root = control.resolve_adopt_library(library)
        category_path = control.adopt_category_path(category)
        control.validate_adopt_category_ancestry(library_root, category_path)
    except Exception as exc:
        raise ImportWorkflowError(str(exc)) from exc

    destination = library_root / category_path / skill
    try:
        control.ensure_inside_root(destination, library_root, "Import destination")
    except Exception as exc:
        raise ImportWorkflowError(str(exc)) from exc
    if os.path.lexists(destination):
        raise ImportWorkflowError(f"Import destination already exists: {control.short(destination)}")

    try:
        acquisition_context = skill_source.acquire_source(source, ref=ref)
        with acquisition_context as acquired:
            source_skill = skill_source.find_skill(acquired, skill)
            control.validate_adopt_skill_tree(source_skill, skill)
            validate_import_payload(source_skill)
            provenance = skill_source.build_provenance(acquired, source_skill, skill)

            if dry_run:
                print(f"IMPORT   {skill}")
                print(f"  source:     {source_skill}")
                print(f"  canonical:  {control.short(destination)}")
                print(f"  provenance: {_format_provenance(provenance)}")
                return 0

            stage_root = Path(
                tempfile.mkdtemp(prefix=f".skill-librarian-import-{skill}-", dir=str(library_root))
            )
            staged_skill = stage_root / skill
            destination_created = False
            try:
                shutil.copytree(
                    source_skill,
                    staged_skill,
                    symlinks=True,
                    ignore=_copy_ignore,
                )
                control.validate_adopt_skill_tree(staged_skill, skill)
                validate_import_payload(staged_skill)
                skill_source.write_provenance(staged_skill, provenance)

                destination.parent.mkdir(parents=True, exist_ok=True)
                control.validate_adopt_category_ancestry(library_root, category_path)
                control.ensure_inside_root(destination, library_root, "Import destination")
                if os.path.lexists(destination):
                    raise ImportWorkflowError(
                        f"Import destination appeared during staging: {control.short(destination)}"
                    )

                os.replace(staged_skill, destination)
                destination_created = True

                discovered, _ = control.discover_skills()
                if discovered.get(skill) != destination.resolve():
                    raise ImportWorkflowError(
                        f"Imported destination is not discoverable as canonical skill '{skill}': "
                        f"{control.short(destination)}"
                    )
            except Exception:
                if destination_created and os.path.lexists(destination):
                    control.remove_any_path(destination)
                raise
            finally:
                shutil.rmtree(stage_root, ignore_errors=True)

    except skill_source.SourceError as exc:
        raise ImportWorkflowError(str(exc)) from exc
    except ImportWorkflowError:
        raise
    except Exception as exc:
        raise ImportWorkflowError(f"Import failed for '{skill}': {exc}") from exc

    print(f"IMPORTED {skill} -> {control.short(destination)}")
    print(f"SOURCE   {_format_provenance(provenance)}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        description="Import one external skill into the canonical library with provenance."
    )
    parser.add_argument("source", help="local path, owner/repo, GitHub URL, or git URL")
    parser.add_argument("--skill", required=True, help="skill name from SKILL.md frontmatter")
    parser.add_argument("--ref", help="git branch, tag, or revision for remote Git sources")
    parser.add_argument(
        "--library",
        metavar="PATH",
        help="configured canonical library root; required when more than one exists",
    )
    parser.add_argument(
        "--category",
        default=DEFAULT_IMPORT_CATEGORY,
        help=f"relative category inside the canonical library (default: {DEFAULT_IMPORT_CATEGORY})",
    )
    parser.add_argument("--dry-run", action="store_true", help="acquire and validate without writing")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return import_skill(
            args.source,
            args.skill,
            library=args.library,
            category=args.category,
            ref=args.ref,
            dry_run=args.dry_run,
        )
    except ImportWorkflowError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
