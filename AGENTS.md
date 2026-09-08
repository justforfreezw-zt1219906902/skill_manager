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

The framework itself provides `skill-librarian`. A personal source library may be flat or grouped by category. Discovery recursively finds directories containing `SKILL.md`, stops at each skill boundary, skips hidden directories, and ignores `retire_skills` by default.

Example:

```text
personal-agent-skills/
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
│       └── SKILL.md
└── retire_skills/
    └── agent-state/
        └── SKILL.md
```

Only `reconstruction-geometry` is active/discoverable in this example. Runtime mounts stay flat: `.agents/skills/reconstruction-geometry`.

## Initial setup

1. Confirm Python 3. `uv` is needed only by individual skills that declare isolated Python dependencies.
2. Copy `deploy.example.json` to git-ignored `deploy.json` and use absolute paths.
3. Point `libraries` at the personal library root, not at one category folder.
4. Keep `retire_skills` in `ignored_directories` unless retirement semantics are intentionally changed.
5. For Codex, use `/Users/<username>/.agents/skills` as the user target.
6. Preview changes before bulk deployment.

Example:

```json
{
  "libraries": ["/Users/<username>/source/personal-agent-skills"],
  "ignored_directories": ["retire_skills"],
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
- Do not discover or mount skills below ignored source directories such as `retire_skills`.
- `doctor` must fail when a runtime link still points into an ignored/retired subtree.
- Avoid mounting the same skill name in both user and project scope.
- Project mounts contain machine-specific absolute links and normally should not be committed.
- Run `doctor` after moving libraries, changing grouping folders, retiring skills, changing configuration, or repairing mounts.

## Migrating/filing skills

When the user wants to move an existing skill into the central library, follow `skill-librarian/SKILL.md`. Category folders are organizational only. The actual skill name remains the basename of the directory containing `SKILL.md` and must match frontmatter `name`.

Do not commit secrets or machine-specific `config.json` files.

## Legacy deployment

`deploy.py` remains backward compatible and uses the same recursive discovery/ignore rules:

```bash
python3 deploy.py --dry-run
python3 deploy.py
python3 deploy.py --skill <name>
```

Prefer scoped `mount`/`unmount` for new Codex workflows because ownership is explicit.
