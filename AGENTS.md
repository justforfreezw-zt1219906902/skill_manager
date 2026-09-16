# skill-librarian — onboarding guide for agents

This repository manages portable agent skills. The central Git-backed skill library is the source of truth; runtime skill directories are deployment views made from links, not independent copies.

## Core model

```text
personal-agent-skills/                  canonical Git source of truth
        |
        +--> skill_manager              control plane
                 |
                 +--> CodexAdapter
                 |      +--> ~/.agents/skills/
                 |      +--> <repo>/.agents/skills/
                 |
                 +--> ClaudeCodeAdapter
                        +--> ~/.claude/skills/
                        +--> <repo>/.claude/skills/
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

Only `reconstruction-geometry` is active/discoverable in this example. Runtime mounts stay flat by skill basename.

## Initial setup

1. Confirm Python 3. `uv` is needed only by individual skills that declare isolated Python dependencies.
2. Copy `deploy.example.json` to git-ignored `deploy.json` and use absolute paths for libraries/user targets.
3. Point `libraries` at the personal library root, not at one category folder.
4. Keep `retire_skills` in `ignored_directories` unless retirement semantics are intentionally changed.
5. Runtime defaults are Codex `~/.agents/skills` + `.agents/skills`, and Claude Code `~/.claude/skills` + `.claude/skills`.
6. Preview risky changes and run `doctor` after configuration or mount changes.

Example:

```json
{
  "libraries": ["/Users/<username>/source/personal-agent-skills"],
  "ignored_directories": ["retire_skills"],
  "runtimes": {
    "codex": {"enabled": true},
    "claude-code": {"enabled": true}
  },
  "targets": [
    "/Users/<username>/.agents/skills",
    "/Users/<username>/.claude/skills"
  ]
}
```

`targets` is legacy bulk-deploy configuration. New scoped workflows should use runtime adapters.

## Preferred scoped management

Use the bundled deterministic CLI instead of writing `ln -s` manually:

```bash
python3 skill-librarian/scripts/skill_librarian.py available
python3 skill-librarian/scripts/skill_librarian.py list --agent all
python3 skill-librarian/scripts/skill_librarian.py doctor --agent all
```

Codex remains the default runtime for backward compatibility. Select another runtime with `--agent` / `-a`, repeat it for multiple runtimes, or use `--agent all`.

### Mount to current project

Codex:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py mount <skill>
```

Claude Code:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount <skill> --agent claude-code
```

Both:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount <skill> --agent all
```

### Mount to user scope

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount <skill> --user --agent all
```

### Unmount

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  unmount <skill> --user --agent codex
```

Use `--user` or `--project REPO` to select scope.

## Runtime status model

`list` and `doctor` classify runtime entries as:

- `MANAGED`: link points to the canonical active source.
- `UNMANAGED`: a real path or external link exists in a runtime directory but is not owned by configured libraries.
- `BROKEN_LINK`: link target is missing outside configured libraries.
- `WRONG_LINK`: the canonical skill name exists, but this runtime link points elsewhere.
- `RETIRED`: link still points into an ignored/retired library subtree.
- `MISSING_SOURCE`: link points into a managed library location whose source disappeared.

Use machine-readable inspection when another tool or agent needs to reason about state:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --agent all --json
```

## Safety rules

- Never delete or overwrite the canonical source skill during mount/unmount.
- `unmount` removes links/junctions only; if a real file/directory is present, stop and report it.
- `mount` refuses to replace an unmanaged real path.
- Use `--force` only to repair an existing wrong link after confirming intent.
- Do not discover or mount skills below ignored source directories such as `retire_skills`.
- `doctor` must fail on unmanaged, broken, wrong, retired, missing-source, or invalidly tracked runtime entries.
- The same skill may be mounted in Codex and Claude Code. Duplicate detection applies to user + project scope inside the same runtime.
- Runtime targets selected together must resolve to distinct physical directories.
- Runtime `user_target` values must be absolute (or `~`-based). Runtime `project_target` values must remain inside a subdirectory of the Git root.
- Project mounts contain machine-specific absolute links and normally should not be committed.
- Run `doctor` after moving libraries, changing grouping folders, retiring skills, changing runtime configuration, or repairing mounts.

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

Prefer scoped `mount`/`unmount` with runtime adapters for new workflows because ownership is explicit.