#!/usr/bin/env python3
"""Registered-project inventory for skill-librarian.

This module intentionally keeps "global" as a read-only aggregate view, not a
third runtime scope. User mounts remain user scope; project mounts remain project
scope. The registry only tells skill-librarian which repositories to inspect.
"""

import argparse
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
CORE_PATH = SCRIPT_PATH.with_name("skill_librarian.py")
REGISTRY_SCHEMA_VERSION = 1


def _load_core():
    spec = importlib.util.spec_from_file_location("skill_librarian_core", CORE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load skill-librarian core from {CORE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()
LibrarianError = core.LibrarianError


def project_registry_path():
    """Return the machine-local project registry path.

    SKILL_LIBRARIAN_PROJECTS_FILE exists mainly for tests and advanced setups.
    On macOS/Linux, XDG_CONFIG_HOME is honored before ~/.config. On Windows,
    APPDATA is used when available.
    """
    explicit = os.environ.get("SKILL_LIBRARIAN_PROJECTS_FILE")
    if explicit:
        return Path(explicit).expanduser().resolve(strict=False)

    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return (Path(base) / "skill-librarian" / "projects.json").resolve(strict=False)
        return (
            Path.home() / "AppData" / "Roaming" / "skill-librarian" / "projects.json"
        ).resolve(strict=False)

    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return (base / "skill-librarian" / "projects.json").resolve(strict=False)


def _normalize_registry_path(raw, label="project"):
    if not isinstance(raw, str) or not raw.strip():
        raise LibrarianError(f"{label} must be a non-empty path")
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve(strict=False)


def read_registered_projects():
    path = project_registry_path()
    if not path.is_file():
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LibrarianError(f"Cannot read project registry {core.short(path)}: {exc}") from exc

    if not isinstance(payload, dict):
        raise LibrarianError(f"Project registry must be a JSON object: {core.short(path)}")
    if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        raise LibrarianError(
            f"Unsupported project registry schema in {core.short(path)}: "
            f"{payload.get('schema_version')!r}"
        )

    raw_projects = payload.get("projects", [])
    if not isinstance(raw_projects, list):
        raise LibrarianError(f"Project registry 'projects' must be a JSON list: {core.short(path)}")

    projects = []
    seen = set()
    for raw in raw_projects:
        if not isinstance(raw, str) or not raw.strip():
            raise LibrarianError(
                f"Project registry entries must be non-empty path strings: {core.short(path)}"
            )
        project = _normalize_registry_path(raw, "registry project")
        if project in seen:
            continue
        seen.add(project)
        projects.append(project)
    return projects


def write_registered_projects(projects):
    path = project_registry_path()
    normalized = sorted(
        {_normalize_registry_path(str(project), "registry project") for project in projects},
        key=lambda item: str(item),
    )
    payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "projects": [str(project) for project in normalized],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=".projects-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return path


def add_project(raw_path):
    root = core.git_root(raw_path, required=True)
    projects = read_registered_projects()
    if root in projects:
        print(f"OK       project already registered: {core.short(root)}")
        return 0
    projects.append(root)
    registry = write_registered_projects(projects)
    print(f"ADDED    project: {core.short(root)}")
    print(f"REGISTRY {core.short(registry)}")
    return 0


def _remove_candidate(raw_path):
    lexical = _normalize_registry_path(raw_path)
    if lexical.exists():
        root = core.git_root(lexical, required=False)
        if root is not None:
            return root
    return lexical


def remove_project(raw_path):
    candidate = _remove_candidate(raw_path)
    projects = read_registered_projects()
    remaining = [project for project in projects if project != candidate]
    if len(remaining) == len(projects):
        print(f"OK       project not registered: {core.short(candidate)}")
        return 0
    registry = write_registered_projects(remaining)
    print(f"REMOVED  project: {core.short(candidate)}")
    print(f"REGISTRY {core.short(registry)}")
    return 0


def project_status(project):
    if not project.exists():
        return "MISSING"
    root = core.git_root(project, required=False)
    if root is None:
        return "NOT_GIT"
    if root != project.resolve(strict=False):
        return "ROOT_CHANGED"
    return "OK"


def list_projects(json_output=False):
    projects = read_registered_projects()
    rows = [
        {"project": str(project), "status": project_status(project)}
        for project in projects
    ]

    if json_output:
        print(
            json.dumps(
                {
                    "registry": str(project_registry_path()),
                    "projects": rows,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not rows:
        print(f"No registered projects. Registry: {core.short(project_registry_path())}")
        return 0

    print(f"{'STATUS':14} PROJECT")
    for row in rows:
        print(f"{row['status']:14} {core.short(row['project'])}")
    return 0


def _annotate_rows(rows, project):
    annotated = []
    for row in rows:
        item = dict(row)
        item["project"] = str(project) if project is not None else None
        annotated.append(item)
    return annotated


def collect_global_rows(agents=None):
    """Collect user scopes plus every registered project scope.

    Returns (rows, warnings). Missing/stale registry entries are skipped and
    surfaced as warnings rather than making the whole inventory unusable.
    """
    skills, _ = core.discover_skills()
    adapters = core.runtime_adapters(agents)
    rows = []
    warnings = []

    for adapter in adapters:
        rows.extend(
            _annotate_rows(
                core.list_target(adapter.name, "user", adapter.user_target(), skills),
                None,
            )
        )

    for registered in read_registered_projects():
        if not registered.exists():
            warnings.append(f"registered project is missing: {core.short(registered)}")
            continue
        root = core.git_root(registered, required=False)
        if root is None:
            warnings.append(f"registered project is not a Git repository: {core.short(registered)}")
            continue
        if root != registered.resolve(strict=False):
            warnings.append(
                "registered project Git root changed: "
                f"{core.short(registered)} -> {core.short(root)}"
            )

        for adapter in adapters:
            try:
                target = adapter.project_target(root)
            except LibrarianError as exc:
                warnings.append(f"{adapter.name}:{core.short(root)}: {exc}")
                continue
            rows.extend(
                _annotate_rows(
                    core.list_target(adapter.name, "project", target, skills),
                    root,
                )
            )

    rows.sort(
        key=lambda row: (
            0 if row["scope"] == "user" else 1,
            row["project"] or "",
            row["runtime"],
            row["name"],
        )
    )
    return rows, warnings


def list_global(agents=None, json_output=False):
    rows, warnings = collect_global_rows(agents=agents)

    if json_output:
        print(
            json.dumps(
                {
                    "registry": str(project_registry_path()),
                    "mounts": rows,
                    "warnings": warnings,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if not rows:
        print("No mounted skills found in user scope or registered project scopes.")
    else:
        project_labels = [core.short(row["project"]) if row["project"] else "-" for row in rows]
        project_width = max(len("PROJECT"), *(len(label) for label in project_labels))
        project_width = min(project_width, 60)
        print(
            f"{'RUNTIME':13} {'SCOPE':8} {'PROJECT':{project_width}} "
            f"{'SKILL':28} {'STATUS':16} TARGET"
        )
        for row, project_label in zip(rows, project_labels):
            if len(project_label) > project_width:
                project_label = "..." + project_label[-(project_width - 3):]
            print(
                f"{row['runtime']:13} {row['scope']:8} {project_label:{project_width}} "
                f"{row['name']:28} {row['status']:16} {core.short(row['target'])}"
            )

    if warnings:
        print("\nNotes:")
        for warning in warnings:
            print(f"  NOTE  {warning}")
    return 0


def add_agent_arg(parser):
    parser.add_argument(
        "-a",
        "--agent",
        dest="agents",
        action="append",
        metavar="RUNTIME",
        help="runtime to inspect: codex, claude-code, or all; repeatable",
    )


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        description="Registered-project global runtime inventory for skill-librarian."
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_list = subs.add_parser("list", help="list user + registered project runtime mounts")
    p_list.add_argument(
        "--global",
        dest="global_view",
        action="store_true",
        help="aggregate user scope and every registered project scope",
    )
    p_list.add_argument("--json", action="store_true", dest="json_output")
    add_agent_arg(p_list)
    p_list.set_defaults(
        handler=lambda args: list_global(agents=args.agents, json_output=args.json_output)
        if args.global_view
        else (_ for _ in ()).throw(LibrarianError("inventory list requires --global"))
    )

    p_projects = subs.add_parser("projects", help="show registered project roots")
    p_projects.add_argument("--json", action="store_true", dest="json_output")
    p_projects.set_defaults(handler=lambda args: list_projects(json_output=args.json_output))

    p_project = subs.add_parser("project", help="manage the registered project roots")
    project_subs = p_project.add_subparsers(dest="project_command", required=True)

    p_add = project_subs.add_parser("add", help="register a Git project root")
    p_add.add_argument("path")
    p_add.set_defaults(handler=lambda args: add_project(args.path))

    p_remove = project_subs.add_parser("remove", help="remove a project from the registry")
    p_remove.add_argument("path")
    p_remove.set_defaults(handler=lambda args: remove_project(args.path))

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except LibrarianError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
