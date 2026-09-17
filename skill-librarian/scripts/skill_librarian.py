#!/usr/bin/env python3
"""Unified skill-librarian CLI entrypoint.

The historical implementation is kept in skill_librarian_core.py and executed
inside this module namespace so existing imports, tests, monkey-patching, and
function globals keep behaving as before. This wrapper adds registered-project
inventory, vendored project skills, and automatic project registration.
"""

import importlib.util
import sys
from pathlib import Path

_WRAPPER_PATH = Path(__file__).resolve()
_CORE_PATH = _WRAPPER_PATH.with_name("skill_librarian_core.py")
_ORIGINAL_NAME = __name__

# Execute the existing implementation in this module's globals. Temporarily use
# a non-main name so the core file's __main__ guard does not fire while loading.
globals()["__name__"] = "skill_librarian_core_embedded"
exec(compile(_CORE_PATH.read_text(encoding="utf-8"), str(_CORE_PATH), "exec"), globals())
globals()["__name__"] = _ORIGINAL_NAME

# Preserve the public paths exactly as if this file still contained the core.
SCRIPT_PATH = _WRAPPER_PATH
SKILL_DIR = SCRIPT_PATH.parent.parent
FRAMEWORK_ROOT = SKILL_DIR.parent
_CORE_MAIN = main


class _ControlFacade:
    """Expose this module's live globals without depending on sys.modules registration."""

    def __getattr__(self, name):
        try:
            return globals()[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


_CONTROL_API = _ControlFacade()


def _load_vendor_module():
    module_path = SCRIPT_PATH.with_name("skill_vendor.py")
    spec = importlib.util.spec_from_file_location("skill_librarian_vendor", module_path)
    if spec is None or spec.loader is None:
        raise LibrarianError(f"Cannot load skill vendor workflow from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_inventory_module():
    module_path = SCRIPT_PATH.with_name("project_inventory.py")
    spec = importlib.util.spec_from_file_location("skill_librarian_inventory", module_path)
    if spec is None or spec.loader is None:
        raise LibrarianError(f"Cannot load project inventory from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # project_inventory intentionally re-loads the control module. Decorate its
    # runtime rows here so a valid physical project snapshot is globally visible
    # as VENDORED rather than looking like an arbitrary UNMANAGED directory.
    vendor = _load_vendor_module()
    original_list_target = module.core.list_target

    def vendor_aware_list_target(runtime, scope, target_dir, skills):
        rows = original_list_target(runtime, scope, target_dir, skills)
        if scope == "project":
            return vendor.annotate_runtime_rows(rows)
        return rows

    module.core.list_target = vendor_aware_list_target
    return module


def _is_inventory_command(argv):
    return bool(argv) and (
        argv[0] in {"projects", "project"}
        or (argv[0] == "list" and "--global" in argv[1:])
    )


def _project_scope_path(argv):
    """Return the project path for successful project-scoped mount/adopt calls."""
    if not argv or argv[0] not in {"mount", "adopt"}:
        return None
    if "--dry-run" in argv or "--user" in argv:
        return None
    if "--project" in argv:
        index = argv.index("--project")
        if index + 1 >= len(argv):
            return None
        return argv[index + 1]
    return "."


def _register_project(path):
    return _load_inventory_module().add_project(path)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "vendor":
        return _load_vendor_module().main(
            argv[1:],
            control=_CONTROL_API,
            register_project=_register_project,
        )

    if _is_inventory_command(argv):
        return _load_inventory_module().main(argv)

    rc = _CORE_MAIN(argv)

    # Going forward, successful project-scoped ownership/mount operations also
    # register their Git root for `list --global`. Existing mounts from older
    # versions still need a one-time `project add <repo>` migration.
    project_path = _project_scope_path(argv)
    if rc == 0 and project_path is not None:
        _register_project(project_path)

    return rc


if _ORIGINAL_NAME == "__main__":
    raise SystemExit(main())
