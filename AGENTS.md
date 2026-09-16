# skill-librarian — onboarding guide for agents

This repository manages portable agent skills. The central Git-backed skill library is the source of truth; runtime skill directories are deployment views made from links, not independent copies once a skill is managed.

## Core model

```text
external/local skill sources
        |
        +--> import + provenance
        |
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
├── imported/
│   └── imported-or-adopted-skill/
│       ├── SKILL.md
│       └── .skill-source.json   # present for imported skills
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
│       └── SKILL.md
└── retire_skills/
    └── agent-state/
        └── SKILL.md
```

Active skills remain flat by basename when mounted into runtimes.

## Initial setup

1. Confirm Python 3. Git is required for project-scoped mounts and Git-backed imports. `uv` is needed only by individual skills that declare isolated Python dependencies.
2. Copy `deploy.example.json` to git-ignored `deploy.json` and use absolute paths for libraries/user targets.
3. Point `libraries` at the personal library root, not at one category folder.
4. Keep `retire_skills` in `ignored_directories` unless retirement semantics are intentionally changed.
5. Runtime defaults are Codex `~/.agents/skills` + `.agents/skills`, and Claude Code `~/.claude/skills` + `.claude/skills`.
6. Preview risky ownership changes and run `doctor` after configuration, mount, or adoption changes.

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

`targets` is legacy bulk-deploy configuration. New workflows should use canonical ownership commands plus runtime adapters.

## Choose the right ownership workflow

Use these distinctions consistently:

```text
external/local source, not installed in runtime
    -> import
    -> canonical library + provenance
    -> explicit mount later if desired

UNMANAGED runtime entry
    -> adopt
    -> canonical library + managed runtime link

already canonical skill
    -> mount / unmount
```

Do not use `npx skills add` as the canonical ownership mechanism. External tools may help discover skills, but `skill_manager import` is responsible for establishing an auditable canonical copy without runtime side effects.

## Import an external skill

GitHub shorthand:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  import vercel-labs/agent-skills --skill frontend-design
```

Pinned Git source:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  import https://github.com/acme/skills --skill postgres-review --ref v1.2.0
```

Local source:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  import /path/to/source --skill postgres-review --category database_skills
```

Use `--dry-run` to acquire and validate without writing. Use `--library` when multiple canonical libraries are configured.

Import rules:

- import creates a canonical asset only; it never mounts into Codex or Claude Code;
- reject duplicate canonical names and unsafe destination categories;
- validate `SKILL.md` and portable links before and after staging;
- reject `.env`-style secret files and common transient dependency/cache directories such as `.venv` and `node_modules`;
- strip nested VCS metadata from the copied skill;
- overwrite any incoming `.skill-source.json` with provenance generated from the actual acquisition;
- sanitize URL credentials/query/fragment before provenance is committed;
- do not persist machine-local `file://` source paths;
- verify discovery after canonicalization and roll back on failure.

A v1 provenance record contains the source type, sanitized source URL when portable, relative source path, requested ref, exact Git revision when available, dirty state for local Git snapshots, skill name, acquisition method, and UTC import timestamp.

After import, inspect the canonical skill and mount it explicitly only if/where needed.

## Preferred runtime management

Use the bundled deterministic CLI instead of writing `ln -s` or manually moving runtime skills:

```bash
python3 skill-librarian/scripts/skill_librarian.py available
python3 skill-librarian/scripts/skill_librarian.py list --agent all
python3 skill-librarian/scripts/skill_librarian.py doctor --agent all
```

Codex remains the default runtime for backward compatibility. Select another runtime with `--agent` / `-a`, repeat it for multiple runtimes, or use `--agent all`. `adopt` deliberately accepts exactly one runtime. `import` has no runtime selection because it does not activate skills.

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

### Adopt an unmanaged runtime skill

When `list` or `doctor` reports a useful runtime skill as `UNMANAGED`, do not manually copy/delete it. Use `adopt` only after the user has explicitly chosen to make it a canonical asset.

Codex user scope:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  adopt <skill> --user --agent codex
```

Project scope:

```bash
python3 /absolute/path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  adopt <skill> --project /path/to/repo --agent claude-code
```

Use `--dry-run` when first inspecting a source/destination. The default destination category is `imported/`. Use `--category` for an intentional active category and `--library` when multiple configured external canonical libraries exist.

`adopt` must remain conservative:

- only one selected runtime and one `UNMANAGED` entry;
- skill name must be a basename, not a path;
- `SKILL.md` frontmatter name must match the basename;
- never overwrite an existing canonical source or destination;
- never adopt into the framework root, hidden categories, ignored categories, or a destination outside the canonical library;
- reject broken/external internal links, absolute internal symlinks, and junction/reparse-point dependencies that would make the copied skill non-portable;
- stage and validate before replacing the runtime entry;
- keep the original runtime backup until final `MANAGED` verification and roll back on failure;
- preserve an external source when the unmanaged runtime entry itself was an external symlink.

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

Treat `UNMANAGED` as an ownership question, not permission to overwrite. Adopt it only when the user wants to preserve it as a canonical asset; otherwise leave it alone or remove it only when explicitly requested.

## Safety rules

- Never delete or overwrite the canonical source skill during mount/unmount.
- Keep external acquisition separate from runtime activation: import first, mount explicitly later.
- `unmount` removes links/junctions only; if a real file/directory is present, stop and report it.
- `mount` refuses to replace an unmanaged real path.
- `adopt` is the explicit unmanaged-to-managed ownership transition; do not simulate it with manual copy/delete commands.
- Use `--force` only to repair an existing wrong link after confirming intent.
- Do not discover, mount, adopt, or import into ignored source directories such as `retire_skills`.
- `doctor` must fail on unmanaged, broken, wrong, retired, missing-source, or invalidly tracked runtime entries.
- The same skill may be mounted in Codex and Claude Code. Duplicate detection applies to user + project scope inside the same runtime.
- Runtime targets selected together must resolve to distinct physical directories.
- Runtime `user_target` values must be absolute (or `~`-based). Runtime `project_target` values must remain inside a subdirectory of the Git root.
- Project mounts contain machine-specific absolute links and normally should not be committed.
- Run `doctor` after moving libraries, changing grouping folders, retiring skills, changing runtime configuration, adopting skills, or repairing mounts.

## Migrating/filing skills

When the user wants to move an existing skill into the central library, follow `skill-librarian/SKILL.md`. Category folders are organizational only. The actual skill name remains the basename of the directory containing `SKILL.md` and must match frontmatter `name`.

If the source is a clean external/local skill, prefer `import` so provenance is captured. If the source already exists as an `UNMANAGED` runtime entry and is self-contained enough to pass validation, prefer `adopt`. If it needs dependency cleanup, secrets extraction, vendoring, or path rewrites first, follow the full migration workflow instead.

Do not commit secrets or machine-specific `config.json` files.

## Legacy deployment

`deploy.py` remains backward compatible and uses the same recursive discovery/ignore rules:

```bash
python3 deploy.py --dry-run
python3 deploy.py
python3 deploy.py --skill <name>
```

Prefer `import`/`adopt` for ownership and scoped `mount`/`unmount` with runtime adapters for activation because ownership is explicit.
