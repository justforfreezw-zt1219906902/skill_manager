#!/usr/bin/env python3
"""
Manage portable agent skills and link them into Codex/user/project scopes.

The source skill library remains the source of truth. Runtime directories contain
links, not copies, so edits or git pulls in the library propagate immediately.

Scoped commands:

    skill-librarian available
    skill-librarian mount NAME [--user | --project REPO]
    skill-librarian unmount NAME [--user | --project REPO]
    skill-librarian list [--user | --project REPO]
    skill-librarian doctor [--user | --project REPO]

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
DEFAULT_USER_TARGET = "~/.agents/skills"
SCOPED_COMMANDS = {"mount", "unmount", "list", "available", "doctor"}


class LibrarianError(RuntimeError):
    pass


def config():
    cfg = FRAMEWORK_ROOT / "deploy.json"
    if cfg.is_file():
        try:
            return json.loads(cfg.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            raise LibrarianError(f"Cannot read {cfg}: {exc}") from exc
    return {}


def short(path):
    path = str(path)
    home = str(Path.home())
    if path == home:
        return "~"
    if path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def configured_library_roots(include_missing=False):
    roots = [FRAMEWORK_ROOT]
    roots.extend(Path(os.path.expanduser(p)).resolve() for p in config().get("libraries", []))

    # A standalone/deployed skill may not have the framework-level deploy.json.
    # Reuse its existing migration config as a fallback library declaration.
    skill_cfg = SKILL_DIR / "config.json"
    if skill_cfg.is_file():
        try:
            local_cfg = json.loads(skill_cfg.read_text())
            library = local_cfg.get("skill_library_path")
            if library:
                roots.append(Path(os.path.expanduser(library)).resolve())
        except (json.JSONDecodeError, OSError):
            pass

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


def discover_skills():
    """Return skill-name -> canonical source folder. First library wins."""
    found = {}
    clashes = []
    for root in configured_library_roots():
        for child in sorted(root.iterdir(), key=lambda p: p.name):
            if child.name.startswith(".") or not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue
            if child.name in found:
                clashes.append((child.name, found[child.name], child))
                continue
            found[child.name] = child.resolve()
    return found, clashes


def user_target():
    cfg = config()
    explicit = cfg.get("user_target")
    if explicit:
        return Path(os.path.expanduser(explicit)).resolve()

    # Backward-compatible with existing deploy.json files that only have targets.
    for target in cfg.get("targets", []):
        expanded = Path(os.path.expanduser(target)).resolve()
        normalized = expanded.as_posix().rstrip("/")
        if normalized.endswith("/.agents/skills"):
            return expanded

    return Path(os.path.expanduser(DEFAULT_USER_TARGET)).resolve()


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


def project_target(path="."):
    return git_root(path, required=True) / ".agents" / "skills"


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


def resolve_scope(user=False, project=None, default_project=True):
    if user and project is not None:
        raise LibrarianError("Choose only one scope: --user or --project REPO")
    if user:
        return "user", user_target()
    if project is not None:
        return "project", project_target(project)
    if default_project:
        return "project", project_target(".")
    return None, None


def mount(skill, user=False, project=None, dry_run=False, force=False):
    skills, _ = discover_skills()
    if skill not in skills:
        available_names = ", ".join(sorted(skills)) or "(none)"
        raise LibrarianError(f"Unknown skill '{skill}'. Available: {available_names}")

    scope, target_dir = resolve_scope(user=user, project=project, default_project=True)
    src = skills[skill]
    link = target_dir / skill
    current = link_target(link)

    if current == src:
        print(f"OK       {skill} already mounted in {scope}: {short(link)}")
        return 0

    if os.path.lexists(link):
        if current is None:
            raise LibrarianError(
                f"Refusing to replace real path {short(link)}. Move it manually if it is intentional."
            )
        if not force:
            raise LibrarianError(
                f"{short(link)} points to {short(current)}, not {short(src)}. "
                "Use --force only if you intend to repair that link."
            )
        if dry_run:
            print(f"REPAIR   {short(link)} -> {short(src)}")
            return 0
        remove_link(link)

    if dry_run:
        print(f"MOUNT    {skill} [{scope}] {short(link)} -> {short(src)}")
        return 0

    target_dir.mkdir(parents=True, exist_ok=True)
    make_link(src, link)
    print(f"MOUNTED  {skill} [{scope}] {short(link)} -> {short(src)}")
    return 0


def unmount(skill, user=False, project=None, dry_run=False):
    scope, target_dir = resolve_scope(user=user, project=project, default_project=True)
    link = target_dir / skill

    if not os.path.lexists(link):
        print(f"OK       {skill} is not mounted in {scope}: {short(link)}")
        return 0

    current = link_target(link)
    if current is None:
        raise LibrarianError(
            f"Refusing to delete real path {short(link)}. unmount removes links only."
        )

    if dry_run:
        print(f"UNMOUNT  {skill} [{scope}] {short(link)}")
        return 0

    remove_link(link)
    print(f"UNMOUNTED {skill} [{scope}] {short(link)}")
    return 0


def describe_entry(entry, known_sources):
    current = link_target(entry)
    if current is not None:
        if not entry.exists():
            return "broken", current
        expected = known_sources.get(entry.name)
        if expected == current:
            return "linked", current
        if expected is None:
            return "external-link", current
        return "wrong-link", current
    if entry.is_dir():
        return "real-dir", entry.resolve()
    return "real-file", entry.resolve()


def list_target(label, target_dir, skills):
    rows = []
    if not target_dir.is_dir():
        return rows
    for entry in sorted(target_dir.iterdir(), key=lambda p: p.name):
        status, resolved = describe_entry(entry, skills)
        rows.append((label, entry.name, status, resolved))
    return rows


def selected_targets(user=False, project=None, include_default_both=False):
    if user and project is not None:
        raise LibrarianError("Choose only one scope: --user or --project REPO")
    if user:
        return [("user", user_target())]
    if project is not None:
        return [("project", project_target(project))]
    if include_default_both:
        result = [("user", user_target())]
        root = git_root(".", required=False)
        if root is not None:
            result.append(("project", root / ".agents" / "skills"))
        return result
    return [("project", project_target("."))]


def list_mounts(user=False, project=None):
    skills, _ = discover_skills()
    rows = []
    for label, target in selected_targets(user=user, project=project, include_default_both=True):
        rows.extend(list_target(label, target, skills))

    if not rows:
        print("No mounted skills found in the selected scope(s).")
        return 0

    print(f"{'SCOPE':8} {'SKILL':28} {'STATUS':14} TARGET")
    for scope, name, status, resolved in rows:
        print(f"{scope:8} {name:28} {status:14} {short(resolved)}")
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


def doctor(user=False, project=None):
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

    targets = selected_targets(user=user, project=project, include_default_both=True)
    mounted_by_scope = {}
    project_root = None
    if any(label == "project" for label, _ in targets):
        project_root = git_root(project if project is not None else ".", required=False)

    for label, target in targets:
        mounted_by_scope[label] = set()
        if not target.exists():
            notes.append(f"{label} target does not exist yet: {short(target)}")
            continue
        if not target.is_dir():
            issues.append(f"{label} target is not a directory: {short(target)}")
            continue
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            mounted_by_scope[label].add(entry.name)
            status, resolved = describe_entry(entry, skills)
            if status == "linked":
                if label == "project" and project_root and git_tracked(project_root, entry):
                    issues.append(
                        f"project mount '{entry.name}' is tracked by Git; local absolute links are machine-specific"
                    )
                continue
            if status == "broken":
                issues.append(f"broken link: {short(entry)} -> {short(resolved)}")
            elif status == "wrong-link":
                issues.append(f"wrong link: {short(entry)} -> {short(resolved)}")
            elif status == "external-link":
                notes.append(
                    f"external link not managed by configured libraries: {short(entry)} -> {short(resolved)}"
                )
            else:
                issues.append(f"real path in managed target ({status}): {short(entry)}")

    duplicate_mounts = mounted_by_scope.get("user", set()) & mounted_by_scope.get("project", set())
    for name in sorted(duplicate_mounts):
        issues.append(f"'{name}' is mounted in both user and project scope")

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

    print("Doctor: healthy. No skill-library or mount problems found.")
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
        raise LibrarianError("No existing target dirs — nothing to deploy into.")

    print(f"Skills:    {', '.join(sorted(skills))}")
    print(f"Targets:   {', '.join(short(t) for t in targets)}")
    print(
        f"Link type: {'junction (Windows)' if os.name == 'nt' else 'symlink'}"
        + ("   [DRY RUN — no changes]" if dry_run else "")
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
                make_link(src, link)
                print(f"  linked    {short(link)}")
            counts["linked"] += 1

    print()
    print("Summary: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


def add_scope_args(parser, allow_force=False):
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--user", action="store_true", help="use ~/.agents/skills user scope")
    group.add_argument("--project", metavar="REPO", help="use REPO/.agents/skills project scope")
    if allow_force:
        parser.add_argument("--force", action="store_true", help="repair an existing wrong link")


def scoped_main(argv):
    parser = argparse.ArgumentParser(description="Manage source-of-truth skills and Codex mounts.")
    subs = parser.add_subparsers(dest="command", required=True)

    p_available = subs.add_parser("available", help="list skills in configured source libraries")
    p_available.set_defaults(handler=lambda a: available())

    p_mount = subs.add_parser("mount", help="mount a skill by symlink/junction")
    p_mount.add_argument("skill")
    add_scope_args(p_mount, allow_force=True)
    p_mount.add_argument("--dry-run", action="store_true")
    p_mount.set_defaults(
        handler=lambda a: mount(
            a.skill, user=a.user, project=a.project, dry_run=a.dry_run, force=a.force
        )
    )

    p_unmount = subs.add_parser("unmount", help="remove a mounted link without touching its source")
    p_unmount.add_argument("skill")
    add_scope_args(p_unmount)
    p_unmount.add_argument("--dry-run", action="store_true")
    p_unmount.set_defaults(
        handler=lambda a: unmount(a.skill, user=a.user, project=a.project, dry_run=a.dry_run)
    )

    p_list = subs.add_parser("list", help="list mounted skills and link health")
    add_scope_args(p_list)
    p_list.set_defaults(handler=lambda a: list_mounts(user=a.user, project=a.project))

    p_doctor = subs.add_parser("doctor", help="diagnose libraries, mounts, duplicates, and broken links")
    add_scope_args(p_doctor)
    p_doctor.set_defaults(handler=lambda a: doctor(user=a.user, project=a.project))

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
