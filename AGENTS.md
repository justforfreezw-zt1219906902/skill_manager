# skill-librarian — onboarding guide for agents

This repository manages portable agent skills. Each skill's source folder is the source of truth; Codex/user/project runtime directories should normally contain symlinks (directory junctions on Windows), not independent copies.

## Core model

```text
central skill library Git repo
        |
        +--> ~/.agents/skills/       Codex user scope
        |
        +--> <repo>/.agents/skills/  Codex project scope
```

The framework itself provides `skill-librarian`, while the user's own skills live in a separate Git-backed library such as `personal-agent-skills/skills`.

## Initial setup

1. Confirm Python 3. `uv` is needed only by individual skills that declare isolated Python dependencies.
2. Copy `deploy.example.json` to git-ignored `deploy.json` and use absolute paths.
3. Put the user's personal library under `libraries`.
4. For Codex, use `/Users/<username>/.agents/skills` as the user target.
5. Preview changes before bulk deployment.

Example:

```json
{
  "libraries": ["/Users/<username>/source/personal-agent-skills/skills"],
  "user_target": "/Users/<username>/.agents/skills",
  "targets": ["/Users/<username>/.agents/skills"]
}
```

## Preferred scoped management

Use the bundled deterministic CLI instead of writing `ln -s` manually:

```bash
python3 skill-librarian/scripts/skill_librarian.py available
python3 skill-librarian/scripts/skill_librarian.py list
python3 skill-librarian/scripts/skill_librarian.py doctor
```

### Mount to current project

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py mount <skill>
```

Default destination:

```text
<git-root>/.agents/skills/<skill>
```

Explicit project:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount <skill> --project /absolute/path/to/repo
```

### Mount to user scope

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount <skill> --user
```

### Unmount

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py unmount <skill>
```

Use `--user` or `--project REPO` to select another scope.

## Safety rules

- Never delete or overwrite the canonical source skill during mount/unmount.
- `unmount` must remove links/junctions only; if a real file/directory is present, stop and report it.
- `mount` must refuse to replace a real file/directory.
- Use `--force` only to repair an existing wrong link after confirming intent.
- Avoid mounting the same skill name in both user and project scope.
- Project mounts contain machine-specific absolute links and normally should not be committed.
- Run `doctor` after moving libraries, changing configuration, or repairing mounts.

## Migrating/filing skills

When the user wants to move an existing skill into the central library, follow `skill-librarian/SKILL.md`. Audit dependencies and machine coupling first, make the result self-contained, verify it, then mount the verified library copy at the correct scope.

Do not commit secrets or machine-specific `config.json` files.

## Legacy deployment

`deploy.py` remains backward compatible:

```bash
python3 deploy.py --dry-run
python3 deploy.py
python3 deploy.py --skill <name>
```

Prefer scoped `mount`/`unmount` for new Codex workflows because ownership is explicit.
