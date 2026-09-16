#!/usr/bin/env python3
"""
Manage portable agent skills and link them into Codex, Claude Code, and project scopes.

The source skill library remains the source of truth. Runtime directories contain
links, not copies, so edits or git pulls in the library propagate immediately.

Configured library roots may contain skills directly or inside grouping folders.
Discovery recurses until it reaches a directory containing SKILL.md, then treats
that directory as a skill boundary. Directories named by ignored_directories are
not scanned; retire_skills is ignored by default.

Scoped commands:

    skill-librarian available
    skill-librarian mount NAME [--user | --project REPO] [-a RUNTIME]
    skill-librarian unmount NAME [--user | --project REPO] [-a RUNTIME]
    skill-librarian list [--user | --project REPO] [-a RUNTIME] [--json]
    skill-librarian doctor [--user | --project REPO] [-a RUNTIME]

Runtime defaults preserve the v0.2 behavior: scoped commands target Codex unless
--agent is supplied. Repeat --agent to target more than one runtime, or use
--agent all. Built-in adapters are codex and claude-code.

For mount/unmount, omitting a scope means the current Git repository.
For list/doctor, omitting a scope inspects user scope plus the current Git
repository when one is available.

The script also keeps the old bulk-deploy interface used by deploy.py:

    python3 deploy.py
    python3 deploy.py --dry-run
    python3 deploy.py --skill NAME

Requires Python 3. Standard library only.
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
SKILL_DIR = SCRIPT_PATH.parent.parent
FRAMEWORK_ROOT = SKILL_DIR.parent
DEFAULT_TARGETS = ["~/.claude/skills", "~/.agents/skills"]
DEFAULT_IGNORED_DIRECTORIES = ("retire_skills",)
DEFAULT_RUNTIME = "codex"
SCOPED_COMMANDS = {"mount", "unmount", "list", "available", "doctor"}

STATUS_MANAGED = "MANAGED"
STATUS_UNMANAGED = "UNMANAGED"
STATUS_BROKEN_LINK = "BROKEN_LINK"
STATUS_WRONG_LINK = "WRONG_LINK"
STATUS_RETIRED = "RETIRED"
STATUS_MISSING_SOURCE = "MISSING_SOURCE"


class LibrarianError(RuntimeError):
    pass


class RuntimeAdapter:
    """Resolve runtime-specific user and project skill directories."""

    def __init__(self, name, default_user_target, project_relative, aliases=()):
        self.name = name
        self.default_user_target = default_user_target
        self.project_relative = Path(project_relative)
        self.aliases = tuple(aliases)

    def runtime_config(self):
        runtimes = config().get("runtimes", {})
        if runtimes is None:
            return {}
        if not isinstance(runtimes, dict):
            raise LibrarianError("runtimes must be a JSON object")
        raw = runtimes.get(self.name, {})
        if raw is False:
            return {"enabled": False}
        if raw is True or raw is None:
            return {}
        if not isinstance(raw, dict):
            raise LibrarianError(f"runtimes.{self.name} must be a JSON object or boolean")
        return raw

    def is_enabled(self):
        return self.runtime_config().get("enabled", True) is not False

    def user_target(self):
        runtime_cfg = self.runtime_config()
        explicit = runtime_cfg.get("user_target")
        if explicit:
            return Path(os.path.expanduser(explicit)).resolve()

        # Backward compatibility: v0.2 used top-level user_target only for Codex.
        if self.name == "codex":
            cfg = config()
            explicit = cfg.get("user_target")
            if explicit:
                return Path(os.path.expanduser(explicit)).resolve()
            for target in cfg.get("targets", []):
                expanded = Path(os.path.expanduser(target)).resolve()
                if expanded.as_posix().rstrip("/").endswith("/.agents/skills"):
                    return expanded

        return Path(os.path.expanduser(self.default_user_target)).resolve()

    def project_target(self, path=".", required=True):
        root = git_root(path, required=required)
        if root is None:
            return None
        runtime_cfg = self.runtime_config()
        relative = runtime_cfg.get("project_target")
        if relative:
            candidate = Path(os.path.expanduser(relative))
            if candidate.is_absolute():
                raise LibrarianError(
                    f"runtimes.{self.name}.project_target must be relative to the Git root"
                )
            return root / candidate
        return root / self.project_relative


RUNTIME_ADAPTERS = {
    "codex": RuntimeAdapter(
        "codex",
        "~/.agents/skills",
        ".agents/skills",
    ),
    "claude-code": RuntimeAdapter(
        "claude-code",
        "~/.claude/skills",
        ".claude/skills",
        aliases=("claude",),
    ),
}

RUNTIME_ALIASES = {
    alias: name
    for name, adapter in RUNTIME_ADAPTERS.items()
    for alias in adapter.aliases
}


def _read_json(path):
    path = Path(path)
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise LibrarianError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise LibrarianError(f"Configuration must be a JSON object: {path}")
    return value


def config():
    return _read_json(FRAMEWORK_ROOT / "deploy.json")


def local_skill_config():
    return _read_json(SKILL_DIR / "config.json")


def short(path):
    path = str(path)
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def ignored_directory_names():
    """Return directory basenames that recursive skill discovery must prune."""
    cfg = config()
    local_cfg = local_skill_config()
    raw = cfg.get("ignored_directories")
    if raw is None:
        raw = local_cfg.get("ignored_directories")
    if raw is None:
        raw = list(DEFAULT_IGNORED_DIRECTORIES)
    if not isinstance(raw, list) or any(not isinstance(name, str) or not name.strip() for name in raw):
        raise LibrarianError("ignored_directories must be a JSON list of non-empty directory names")
    return {name.strip() for name in raw}


def configured_library_roots(include_missing=False):
    roots = [FRAMEWORK_ROOT]
    roots.extend(Path(os.path.expanduser(p)).resolve() for p in config().get("libraries", []))

    # A standalone/deployed skill may not have the framework-level deploy.json.
    # Reuse its existing migration config as a fallback library declaration.
    library = local_skill_config().get("skill_library_path")
    if library:
        roots.append(Path(os.path.expanduser(library)).resolve())

    seen = set()
    out = []
    for root in roots:
        root = root.resolve()
        if root in seen:
            continue
        seen.add(root)
        if root.is_dir() or include_missing:
            out.append(root)
    return out


def iter_skill_dirs(root):
    """Yield lexical skill folders below root in deterministic relative-path order."""
    root = Path(root).resolve()
    ignored = ignored_directory_names()
    found = []

    def walk(directory):
        try:
            children = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            raise LibrarianError(f"Cannot scan skill library directory {directory}: {exc}") from exc
        for child in children:
            if child.name.startswith(".") or child.name in ignored or not child.is_dir():
                continue
            if (child / "SKILL.md").is_file():
                found.append(child)
                continue
            # Do not recurse through grouping-directory symlinks. A symlink that is itself
            # a skill is still accepted above for backward compatibility.
            if child.is_symlink():
                continue
            walk(child)

    walk(root)
    found.sort(key=lambda p: p.relative_to(root).as_posix())
    return found


def discover_skills():
    """Return skill-name -> canonical source folder. First library wins."""
    found = {}
    clashes = []
    for root in configured_library_roots():
        for child in iter_skill_dirs(root):
            name = child.name
            resolved = child.resolve()
            if name in found:
                clashes.append((name, found[name], resolved))
                continue
            found[name] = resolved
    return found, clashes


def git_root(path=".", required=True):
    candidate = Path(path).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
        return Path(proc.stdout.strip()).resolve()
    except (subprocess.CalledProcessError, FileNotFoundError):
        if required:
            raise LibrarianError(f"Not inside a Git repository: {candidate}")
        return None


def normalize_runtime_name(name):
    name = name.strip().lower()
    return RUNTIME_ALIASES.get(name, name)


def runtime_adapters(agents=None):
    """Resolve CLI agent selectors to distinct built-in runtime adapters."""
    requested = list(agents or [DEFAULT_RUNTIME])
    if not requested:
        requested = [DEFAULT_RUNTIME]

    normalized = [normalize_runtime_name(value) for value in requested]
    if "all" in normalized:
        if len(normalized) > 1:
            raise LibrarianError("--agent all cannot be combined with other --agent values")
        names = [name for name, adapter in RUNTIME_ADAPTERS.items() if adapter.is_enabled()]
    else:
        names = normalized

    result = []
    seen = set()
    for name in names:
        adapter = RUNTIME_ADAPTERS.get(name)
        if adapter is None:
            available = ", ".join(sorted(RUNTIME_ADAPTERS))
            raise LibrarianError(f"Unknown runtime '{name}'. Available: {available}, all")
        if not adapter.is_enabled():
            raise LibrarianError(f"Runtime '{name}' is disabled in deploy.json")
        if name not in seen:
            seen.add(name)
            result.append(adapter)
    return result


def user_target():
    """Backward-compatible Codex user target accessor."""
    return RUNTIME_ADAPTERS["codex"].user_target()


def project_target(path="."):
    """Backward-compatible Codex project target accessor."""
    return RUNTIME_ADAPTERS["codex"].project_target(path)


def link_target(path):
    """Return resolved target for a symlink/junction, otherwise None."""
    path = Path(path)
    if not os.path.lexists(path):
        return None
    if path.is_symlink():
        return Path(os.path.realpath(path)).resolve()
    if os.name == "nt" and path.is_dir():
        try:
            if os.lstat(path).st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return Path(os.path.realpath(path)).resolve()
        except (AttributeError, OSError):
            pass
    return None


def remove_link(path):
    path = Path(path)
    if os.name == "nt":
        try:
            os.rmdir(path)
        except OSError:
            os.unlink(path)
    else:
        path.unlink()


def make_link(src, link):
    src = Path(src).resolve()
    link = Path(link)
    if os.name == "nt":
        try:
            import _winapi
            _winapi.CreateJunction(str(src), str(link))
        except (ImportError, AttributeError, OSError):
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(src)],
                check=True,
                capture_output=True,
            )
    else:
        os.symlink(src, link)


def resolve_scope(adapter, user=False, project=None, default_project=True):
    if user and project is not None:
        raise LibrarianError("Choose only one scope: --user or --project REPO")
    if user:
        return "user", adapter.user_target()
    if project is not None:
        return "project", adapter.project_target(project)
    if default_project:
        return "project", adapter.project_target(".")
    return None, None


def _mount_plan(skill, source, adapters, user=False, project=None, force=False):
    plan = []
    for adapter in adapters:
        scope, target_dir = resolve_scope(adapter, user=user, project=project, default_project=True)
        link = target_dir / skill
        current = link_target(link)
        action = "mount"
        if current == source:
            action = "ok"
        elif os.path.lexists(link):
            if current is None:
                raise LibrarianError(
                    f"Refusing to replace unmanaged real path {short(link)} for {adapter.name}. "
                    "Adopt or move it manually first."
                )
            if not force:
                raise LibrarianError(
                    f"{short(link)} for {adapter.name} points to {short(current)}, not {short(source)}. "
                    "Use --force only if you intend to repair that link."
                )
            action = "repair"
        plan.append((adapter, scope, target_dir, link, current, action))
    return plan


def mount(skill, user=False, project=None, dry_run=False, force=False, agents=None):
    skills, _ = discover_skills()
    if skill not in skills:
        available_names = ", ".join(sorted(skills)) or "(none)"
        raise LibrarianError(f"Unknown skill '{skill}'. Available: {available_names}")

    source = skills[skill]
    adapters = runtime_adapters(agents)
    plan = _mount_plan(skill, source, adapters, user=user, project=project, force=force)

    for adapter, scope, target_dir, link, current, action in plan:
        label = f"{adapter.name}:{scope}"
        if action == "ok":
            print(f"OK       {skill} already mounted [{label}]: {short(link)}")
            continue
        if dry_run:
            verb = "REPAIR" if action == "repair" else "MOUNT"
            print(f"{verb:8} {skill} [{label}] {short(link)} -> {short(source)}")
            continue
        if action == "repair":
            remove_link(link)
        target_dir.mkdir(parents=True, exist_ok=True)
        make_link(source, link)
        verb = "REPAIRED" if action == "repair" else "MOUNTED"
        print(f"{verb:8} {skill} [{label}] {short(link)} -> {short(source)}")
    return 0


def unmount(skill, user=False, project=None, dry_run=False, agents=None):
    adapters = runtime_adapters(agents)
    plan = []
    for adapter in adapters:
        scope, target_dir = resolve_scope(adapter, user=user, project=project, default_project=True)
        link = target_dir / skill
        if not os.path.lexists(link):
            plan.append((adapter, scope, link, None, "missing"))
            continue
        current = link_target(link)
        if current is None:
            raise LibrarianError(
                f"Refusing to delete unmanaged real path {short(link)} for {adapter.name}. "
                "unmount removes links only."
            )
        plan.append((adapter, scope, link, current, "remove"))

    for adapter, scope, link, current, action in plan:
        label = f"{adapter.name}:{scope}"
        if action == "missing":
            print(f"OK       {skill} is not mounted [{label}]: {short(link)}")
            continue
        if dry_run:
            print(f"UNMOUNT  {skill} [{label}] {short(link)}")
            continue
        remove_link(link)
        print(f"UNMOUNTED {skill} [{label}] {short(link)}")
    return 0


def _relative_to_library(path):
    """Return (root, relative) when path is lexically below a configured library."""
    candidate = Path(path)
    try:
        candidate = candidate.resolve(strict=False)
    except TypeError:
        candidate = Path(os.path.abspath(candidate))
    for root in configured_library_roots(include_missing=True):
        root = root.resolve()
        try:
            return root, candidate.relative_to(root)
        except ValueError:
            continue
    return None, None


def is_ignored_library_source(path):
    """Return True when path is inside an ignored subtree of a configured library."""
    root, relative = _relative_to_library(path)
    if root is None:
        return False
    ignored = ignored_directory_names()
    return any(part in ignored for part in relative.parts)


def is_managed_library_path(path):
    root, relative = _relative_to_library(path)
    return root is not None and not any(part in ignored_directory_names() for part in relative.parts)


def describe_entry(entry, known_sources):
    """Classify one runtime entry against the canonical source libraries."""
    entry = Path(entry)
    current = link_target(entry)
    if current is not None:
        expected = known_sources.get(entry.name)
        if not entry.exists():
            if is_ignored_library_source(current):
                return STATUS_RETIRED, current
            if is_managed_library_path(current):
                return STATUS_MISSING_SOURCE, current
            return STATUS_BROKEN_LINK, current
        if expected == current:
            return STATUS_MANAGED, current
        if expected is not None:
            return STATUS_WRONG_LINK, current
        if is_ignored_library_source(current):
            return STATUS_RETIRED, current
        # An externally created link is still unmanaged from skill-librarian's point of view.
        return STATUS_UNMANAGED, current

    if os.path.lexists(entry):
        try:
            return STATUS_UNMANAGED, entry.resolve()
        except OSError:
            return STATUS_UNMANAGED, entry
    return STATUS_BROKEN_LINK, entry


def list_target(runtime, scope, target_dir, skills):
    rows = []
    if not target_dir.is_dir():
        return rows
    for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
        status, resolved = describe_entry(entry, skills)
        rows.append(
            {
                "runtime": runtime,
                "scope": scope,
                "name": entry.name,
                "status": status,
                "path": str(entry),
                "target": str(resolved),
            }
        )
    return rows


def selected_targets(user=False, project=None, include_default_both=False, agents=None):
    if user and project is not None:
        raise LibrarianError("Choose only one scope: --user or --project REPO")

    result = []
    for adapter in runtime_adapters(agents):
        if user:
            result.append((adapter.name, "user", adapter.user_target()))
            continue
        if project is not None:
            result.append((adapter.name, "project", adapter.project_target(project)))
            continue
        if include_default_both:
            result.append((adapter.name, "user", adapter.user_target()))
            project_dir = adapter.project_target(".", required=False)
            if project_dir is not None:
                result.append((adapter.name, "project", project_dir))
            continue
        result.append((adapter.name, "project", adapter.project_target(".")))
    return result


def collect_mount_rows(user=False, project=None, agents=None):
    skills, _ = discover_skills()
    rows = []
    for runtime, scope, target in selected_targets(
        user=user,
        project=project,
        include_default_both=True,
        agents=agents,
    ):
        rows.extend(list_target(runtime, scope, target, skills))
    return rows


def list_mounts(user=False, project=None, agents=None, json_output=False):
    rows = collect_mount_rows(user=user, project=project, agents=agents)

    if json_output:
        print(json.dumps({"mounts": rows}, indent=2, sort_keys=True))
        return 0

    if not rows:
        print("No mounted skills found in the selected runtime/scope(s).")
        return 0

    print(f"{'RUNTIME':13} {'SCOPE':8} {'SKILL':28} {'STATUS':16} TARGET")
    for row in rows:
        print(
            f"{row['runtime']:13} {row['scope']:8} {row['name']:28} "
            f"{row['status']:16} {short(row['target'])}"
        )
    return 0


def available():
    skills, clashes = discover_skills()
    if not skills:
        print("No skills found in configured libraries.")
        return 0
    print(f"{'SKILL':28} SOURCE")
    for name, source in sorted(skills.items()):
        print(f"{name:28} {short(source)}")
    if clashes:
        print("\nName clashes (first library wins):")
        for name, kept, ignored in clashes:
            print(f"  {name}: kept {short(kept)}; ignored {short(ignored)}")
    return 0


def parse_frontmatter_name(skill_md):
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:40]:
        if line.strip() == "---":
            break
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"\'')
    return None


def git_tracked(project_root, path):
    try:
        lexical = Path(os.path.abspath(path))
        rel = lexical.relative_to(project_root)
    except ValueError:
        return False
    proc = subprocess.run(
        ["git", "-C", str(project_root), "ls-files", "--error-unmatch", "--", str(rel)],
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def doctor(user=False, project=None, agents=None):
    issues = []
    notes = []

    cfg = config()
    for raw in cfg.get("libraries", []):
        root = Path(os.path.expanduser(raw)).resolve()
        if not root.is_dir():
            issues.append(f"configured library is missing: {short(root)}")

    skills, clashes = discover_skills()
    for name, source in skills.items():
        manifest = source / "SKILL.md"
        fm_name = parse_frontmatter_name(manifest)
        if fm_name is None:
            issues.append(f"{name}: SKILL.md has no readable 'name' frontmatter")
        elif fm_name != name:
            issues.append(f"{name}: frontmatter name is '{fm_name}'")

    for name, kept, ignored in clashes:
        issues.append(
            f"duplicate source name '{name}': using {short(kept)}, ignoring {short(ignored)}"
        )

    targets = selected_targets(
        user=user,
        project=project,
        include_default_both=True,
        agents=agents,
    )
    mounted_by_runtime_scope = {}
    project_roots = {}

    for runtime, scope, target in targets:
        key = (runtime, scope)
        mounted_by_runtime_scope[key] = set()
        if scope == "project":
            project_roots[runtime] = git_root(project if project is not None else ".", required=False)

        if not target.exists():
            notes.append(f"{runtime}:{scope} target does not exist yet: {short(target)}")
            continue
        if not target.is_dir():
            issues.append(f"{runtime}:{scope} target is not a directory: {short(target)}")
            continue

        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            mounted_by_runtime_scope[key].add(entry.name)
            status, resolved = describe_entry(entry, skills)
            label = f"{runtime}:{scope}"
            if status == STATUS_MANAGED:
                project_root = project_roots.get(runtime)
                if scope == "project" and project_root and git_tracked(project_root, entry):
                    issues.append(
                        f"{label} mount '{entry.name}' is tracked by Git; local absolute links are machine-specific"
                    )
                continue
            if status == STATUS_UNMANAGED:
                issues.append(f"{label} unmanaged runtime entry: {short(entry)} -> {short(resolved)}")
            elif status == STATUS_BROKEN_LINK:
                issues.append(f"{label} broken link: {short(entry)} -> {short(resolved)}")
            elif status == STATUS_MISSING_SOURCE:
                issues.append(f"{label} managed source is missing: {short(entry)} -> {short(resolved)}")
            elif status == STATUS_WRONG_LINK:
                issues.append(f"{label} wrong link: {short(entry)} -> {short(resolved)}")
            elif status == STATUS_RETIRED:
                issues.append(f"{label} retired skill is still mounted: {short(entry)} -> {short(resolved)}")
            else:
                issues.append(f"{label} unknown status {status}: {short(entry)}")

    # Duplicate user/project mounts are only conflicts inside the same runtime.
    for runtime in {runtime for runtime, _scope, _target in targets}:
        duplicate_mounts = mounted_by_runtime_scope.get((runtime, "user"), set()) & mounted_by_runtime_scope.get(
            (runtime, "project"), set()
        )
        for name in sorted(duplicate_mounts):
            issues.append(f"{runtime}: '{name}' is mounted in both user and project scope")

    if notes:
        print("Notes:")
        for note in notes:
            print(f"  NOTE  {note}")
    if issues:
        print("Issues:")
        for issue in issues:
            print(f"  FAIL  {issue}")
        print(f"\nDoctor found {len(issues)} issue(s).")
        return 1

    print("Doctor: healthy. No skill-library or runtime mount problems found.")
    return 0


def legacy_targets():
    seen = set()
    result = []
    for raw in config().get("targets", DEFAULT_TARGETS):
        target = Path(os.path.expanduser(raw)).resolve()
        if target in seen:
            continue
        seen.add(target)
        if target.is_dir():
            result.append(target)
        else:
            print(f"  (configured target doesn't exist, skipping: {short(target)})")
    return result


def deploy_all(dry_run=False, only=None):
    skills, clashes = discover_skills()
    for name, kept, ignored in clashes:
        print(f"  (name clash: '{name}'; keeping {short(kept)}, ignoring {short(ignored)})")
    if only:
        if only not in skills:
            raise LibrarianError(f"No skill named '{only}' in any configured library.")
        skills = {only: skills[only]}

    targets = legacy_targets()
    if not targets:
        raise LibrarianError("No existing target dirs - nothing to deploy into.")

    print(f"Skills:    {', '.join(sorted(skills))}")
    print(f"Targets:   {', '.join(short(t) for t in targets)}")
    print(
        f"Link type: {'junction (Windows)' if os.name == 'nt' else 'symlink'}"
        + ("   [DRY RUN - no changes]" if dry_run else "")
    )
    print()

    counts = {"linked": 0, "ok": 0, "repaired": 0, "replaced": 0, "skipped": 0}
    for skill, src in sorted(skills.items()):
        for target in targets:
            link = target / skill
            current = link_target(link)
            if current == src:
                counts["ok"] += 1
                continue
            if current is not None:
                if dry_run:
                    print(f"  REPAIR    {short(link)}  (relink -> {short(src)})")
                else:
                    remove_link(link)
                    make_link(src, link)
                    print(f"  repaired  {short(link)}")
                counts["repaired"] += 1
                continue
            if os.path.lexists(link):
                if dry_run:
                    print(f"  REAL DIR  {short(link)}  (would prompt to replace)")
                    counts["skipped"] += 1
                    continue
                ans = input(
                    f"  {short(link)} is a real directory (a drifted copy). "
                    f"Replace it with a link to {short(src)}? [y/N] "
                )
                if ans.strip().lower() == "y":
                    if link.is_dir() and not link.is_symlink():
                        shutil.rmtree(link)
                    else:
                        link.unlink()
                    make_link(src, link)
                    print(f"  replaced  {short(link)}")
                    counts["replaced"] += 1
                else:
                    print(f"  skipped   {short(link)}")
                    counts["skipped"] += 1
                continue
            if dry_run:
                print(f"  LINK      {short(link)}")
            else:
                target.mkdir(parents=True, exist_ok=True)
                make_link(src, link)
                print(f"  linked    {short(link)}")
            counts["linked"] += 1

    print()
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def add_scope_args(parser, allow_force=False, allow_agent=True):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--user", action="store_true", help="use runtime user scope")
    group.add_argument("--project", metavar="REPO", help="use runtime project scope under REPO")
    if allow_agent:
        parser.add_argument(
            "-a",
            "--agent",
            dest="agents",
            action="append",
            metavar="RUNTIME",
            help="runtime to manage: codex, claude-code, or all; repeatable",
        )
    if allow_force:
        parser.add_argument("--force", action="store_true", help="repair an existing wrong link")


def scoped_main(argv):
    parser = argparse.ArgumentParser(description="Manage source-of-truth skills and runtime mounts.")
    subs = parser.add_subparsers(dest="command", required=True)

    p_available = subs.add_parser("available", help="list skills in configured source libraries")
    p_available.set_defaults(handler=lambda a: available())

    p_mount = subs.add_parser("mount", help="mount a skill by symlink/junction")
    p_mount.add_argument("skill")
    add_scope_args(p_mount, allow_force=True)
    p_mount.add_argument("--dry-run", action="store_true")
    p_mount.set_defaults(
        handler=lambda a: mount(
            a.skill,
            user=a.user,
            project=a.project,
            dry_run=a.dry_run,
            force=a.force,
            agents=a.agents,
        )
    )

    p_unmount = subs.add_parser("unmount", help="remove a mounted link without touching its source")
    p_unmount.add_argument("skill")
    add_scope_args(p_unmount)
    p_unmount.add_argument("--dry-run", action="store_true")
    p_unmount.set_defaults(
        handler=lambda a: unmount(
            a.skill,
            user=a.user,
            project=a.project,
            dry_run=a.dry_run,
            agents=a.agents,
        )
    )

    p_list = subs.add_parser("list", help="list runtime skills and management status")
    add_scope_args(p_list)
    p_list.add_argument("--json", action="store_true", dest="json_output", help="emit machine-readable JSON")
    p_list.set_defaults(
        handler=lambda a: list_mounts(
            user=a.user,
            project=a.project,
            agents=a.agents,
            json_output=a.json_output,
        )
    )

    p_doctor = subs.add_parser("doctor", help="diagnose libraries, mounts, duplicates, and unmanaged skills")
    add_scope_args(p_doctor)
    p_doctor.set_defaults(
        handler=lambda a: doctor(user=a.user, project=a.project, agents=a.agents)
    )

    args = parser.parse_args(argv)
    return args.handler(args)


def legacy_main(argv):
    parser = argparse.ArgumentParser(
        description="Link skills from this repo + configured libraries into runtime skill dirs."
    )
    parser.add_argument("--dry-run", action="store_true", help="show actions, change nothing")
    parser.add_argument("--skill", metavar="NAME", help="deploy just one skill")
    args = parser.parse_args(argv)
    return deploy_all(dry_run=args.dry_run, only=args.skill)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if argv and argv[0] in SCOPED_COMMANDS:
            return scoped_main(argv)
        return legacy_main(argv)
    except LibrarianError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
