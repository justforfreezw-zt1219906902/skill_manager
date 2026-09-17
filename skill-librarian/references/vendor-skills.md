# Vendored project skills

Use vendoring when a project should own a physical, version-pinnable skill snapshot instead of a machine-local symlink/junction to the canonical library.

Commands:

```bash
python3 skill-librarian/scripts/skill_librarian.py vendor <skill> --project <repo> --agent codex
python3 skill-librarian/scripts/skill_librarian.py vendor status <skill> --project <repo> --agent codex
python3 skill-librarian/scripts/skill_librarian.py vendor update <skill> --project <repo> --agent codex
python3 skill-librarian/scripts/skill_librarian.py vendor remove <skill> --project <repo> --agent codex
```

A vendored copy is a real directory under the runtime project target and contains `.skill-vendor.json`. `list --global` reports valid vendored directories as `VENDORED`, while ordinary real directories without valid ownership metadata remain `UNMANAGED`.

`vendor status` reports `SAME`, `SOURCE_CHANGED`, `LOCAL_MODIFIED`, `DIVERGED`, `SOURCE_MISSING`, or `SOURCE_MISSING_LOCAL_MODIFIED` by comparing the recorded baseline fingerprint, project copy, and current canonical skill.

`vendor update` refuses to overwrite local modifications unless `--force` is explicit. `vendor remove` likewise refuses to delete a locally modified vendored copy unless `--force` is explicit. Create, update, and remove support `--dry-run`.

Successful vendor creation automatically registers the Git project for global inventory. Vendored copies are suitable for committing with the project; mounted symlink/junction entries are normally machine-specific and should not be committed.
