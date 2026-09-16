---
name: skill-librarian
description: Manage a Git-backed central agent-skill library and deploy skills safely across Codex and Claude Code runtime scopes. Use when the user wants to add/migrate or adopt a skill into the library, organize or retire skills, list available skills, mount/unmount at user or project scope, inspect runtime state, detect unmanaged skills, repair links, or diagnose skill-library problems. Supports recursive grouped source libraries, ignored retirement folders, Codex and Claude Code runtime adapters, strict runtime status classification, safe unmanaged-to-managed adoption, and JSON inspection while keeping the central library authoritative.
---

# Skill Librarian

Treat the central skill library as the source of truth. Runtime skill directories are deployment views made of links, not independent copies.

Use this model:

```text
personal-agent-skills/                  canonical Git source of truth
        |
        +-- category folders (optional)
        +-- retire_skills/ (ignored by default)
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

Keep normal skill edits in the central source library. Do not edit mounted copies as independent sources.

## Source-library organization

Allow either flat or grouped source layouts:

```text
personal-agent-skills/
├── generic-skill/
│   └── SKILL.md
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
│       └── SKILL.md
└── retire_skills/
    └── agent-state/
        └── SKILL.md
```

The deterministic CLI searches configured library roots recursively. A directory containing `SKILL.md` is a skill boundary; use its basename as the skill name and do not discover anything below it. Skip hidden directories. Ignore `retire_skills` by default.

Category folder names are organizational only. Runtime mounts stay flat by skill basename.

## Configuration

`config.json` in this skill is machine-specific and git-ignored. Copy `config.example.json` when needed.

Supported local keys:

- `skill_library_path`: absolute path to the user's central skill-library root.
- `ignored_directories`: source-directory basenames to prune during recursive discovery. Defaults to `["retire_skills"]`.
- `state_root`: absolute path for skill-owned persistent state used by migration workflows.

The framework-level `deploy.json` may contain:

```json
{
  "libraries": ["/absolute/path/to/personal-agent-skills"],
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

Runtime defaults:

| Runtime | User scope | Project scope |
| --- | --- | --- |
| Codex | `~/.agents/skills` | `<repo>/.agents/skills` |
| Claude Code | `~/.claude/skills` | `<repo>/.claude/skills` |

Optional per-runtime overrides:

```json
{
  "runtimes": {
    "codex": {
      "enabled": true,
      "user_target": "/absolute/custom/codex/skills",
      "project_target": ".agents/skills"
    }
  }
}
```

Runtime `user_target` must be absolute (or `~`-based). `project_target` must be a relative subdirectory that stays inside the Git root. Unknown runtime names/config keys are errors. `targets` remains legacy bulk-deploy configuration.

Framework `deploy.json` takes precedence for `ignored_directories`; a standalone/deployed `skill-librarian` falls back to local `config.json`.

Use absolute paths in machine config. Never put secrets in committed files.

## Skill management

Use the bundled deterministic CLI for link management and ownership transitions. Do not hand-write `ln -s` when the CLI is available.

Set:

```bash
SKILL_DIR=<absolute path to the directory containing this SKILL.md>
```

Then use:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
python "$SKILL_DIR/scripts/skill_librarian.py" list --agent all
python "$SKILL_DIR/scripts/skill_librarian.py" doctor --agent all
```

Codex is the default runtime for compatibility. Use `--agent codex`, `--agent claude-code`, repeat `--agent`, or use `--agent all`. `adopt` is intentionally single-runtime because it takes ownership of one concrete runtime entry.

### Mount a project skill

Codex default:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill>
```

Claude Code:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --agent claude-code
```

Both supported runtimes:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --agent all
```

Explicit repo:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  mount <skill> --project /absolute/path/to/repo --agent all
```

### Mount a user skill

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user --agent all
```

### Unmount

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  unmount <skill> --user --agent codex
```

`unmount` removes links/junctions only. If the target is a real directory or file, stop and report it; never delete it automatically.

### Adopt an unmanaged runtime skill

Use `adopt` only when runtime inspection reports the entry as `UNMANAGED` and the user wants that skill to become a canonical library asset.

Codex user scope:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt <skill> --user --agent codex
```

Project scope:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt <skill> --project /path/to/repo --agent claude-code
```

The default canonical category is `imported/`. Use `--category <relative-category>` for a known durable classification. If multiple external canonical libraries are configured, select one with `--library /absolute/path/to/library`. Use `--dry-run` before mutation when the source or destination is unfamiliar.

Adopt is a controlled ownership transition:

```text
UNMANAGED runtime directory/link
        |
        +--> stage + validate copy
        |
        +--> canonical library/<category>/<skill>
        |
        +--> runtime entry replaced by managed link
        |
        +--> verify MANAGED
```

Required safety behavior:

- accept exactly one runtime and one runtime entry;
- validate the skill argument as a simple basename, never a path;
- require a readable `SKILL.md` whose `name` matches the runtime basename;
- adopt only into a configured external canonical library, never the skill_manager framework root;
- refuse existing canonical skill-name or destination collisions;
- refuse hidden/ignored/escaping destination categories;
- refuse broken/external internal links, absolute internal symlinks, and junction/reparse-point dependencies that would make the copied skill non-portable;
- stage and revalidate the copy before touching the runtime entry;
- roll back the original runtime entry and canonical destination if managed-link creation fails;
- if the unmanaged runtime entry is itself an external symlink, preserve that external source and replace only the runtime link.

### Available

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
```

List active canonical source skills only. Do not list skills below ignored directories such as `retire_skills`.

### Runtime inspection

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" list --agent all
python "$SKILL_DIR/scripts/skill_librarian.py" list --agent all --json
```

Statuses:

- `MANAGED`: runtime entry points at the canonical active source.
- `UNMANAGED`: real path or external link exists in a runtime directory but is not owned by configured libraries.
- `BROKEN_LINK`: link target is missing outside configured libraries.
- `WRONG_LINK`: canonical skill name exists, but runtime link points elsewhere.
- `RETIRED`: runtime still points into an ignored/retired source subtree.
- `MISSING_SOURCE`: runtime points into a managed library location whose source disappeared.

A skill installed directly by Codex, Claude Code, `npx skills`, or another tool is intentionally `UNMANAGED` until the user explicitly adopts or removes it.

### Doctor

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" doctor --agent all
```

Use it after changing libraries, regrouping skills, retiring skills, moving repos, changing runtime config, mounting/unmounting, or adopting skills. It checks for:

- missing configured libraries;
- malformed or mismatched `SKILL.md` names;
- duplicate active skill names across source paths;
- unmanaged runtime entries;
- broken, wrong, retired, or missing-source links;
- invalid runtime configuration;
- runtime target collisions;
- the same skill mounted at both user and project scope inside one runtime;
- project mounts accidentally tracked by Git.

Read `references/skill-management.md` for exact discovery, runtime, status, adoption, and safety behavior.

## Safety rules for mount and adoption management

- Treat source skill folders as immutable from mount/unmount operations.
- Never copy a skill merely to deploy it unless symlinks/junctions are impossible and the user explicitly asks for a copy.
- Never replace an unmanaged real target automatically; use `adopt` only for an explicit ownership transition.
- Multi-runtime mount/unmount operations must preflight all selected targets before mutation.
- Reject selected runtime/scope targets that resolve to the same physical directory.
- Only use `--force` to repair an existing wrong link that points at the wrong active source, after confirming that repair is intended.
- Do not mount or adopt into ignored/retired source folders.
- If `doctor` reports a retired link, unmount it explicitly; do not silently relink it elsewhere.
- Project mounts are normally local machine state because they point to absolute source paths. Do not commit those links unless the project explicitly wants that machine-specific behavior.
- The same skill may be mounted in Codex and Claude Code. Normally do not mount the same skill at both user and project scope within one runtime.

## Legacy bulk deployment

The root `deploy.py` remains supported for backward compatibility:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill <name>
```

Legacy deployment uses the `targets` array plus the same recursive discovery and ignore rules. Prefer scoped `mount`/`unmount`/`adopt` commands with runtime adapters for new workflows.

## Migrating or filing a skill into the library

Use the migration workflow only when the source skill is not already a clean, self-contained library skill.

### 1. Locate and choose category

Take the source skill path from the user. Read its `SKILL.md`. Determine the intended skill name and, when the library is grouped, the appropriate active category directory.

Check active discovery by skill **basename**, not only by destination path. If the same active skill name already exists elsewhere in the library, stop rather than creating a collision.

Do not migrate an active skill into `retire_skills`; that directory is for intentionally inactive skills.

### 2. Audit dependencies

Classify everything the skill depends on:

- external runnable code -> vendor into `scripts/`;
- reference docs/rules -> vendor into `references/`;
- another skill -> reference it, do not copy it;
- user-provided secrets and machine paths -> move to git-ignored `config.json`;
- tool-managed credentials/state -> move under `<state_root>/<skill-name>/`;
- outputs -> leave where the user expects, configured when necessary.

Do not migrate `.venv`, `node_modules`, build output, caches, or `.env` files.

For Python skill scripts, prefer isolated dependencies with `uv`; do not require global `pip install` state.

### 3. Human gate

Before changing an existing source skill, summarize the dependency audit, proposed migration/category, configuration keys, and any non-obvious decisions. If the user already explicitly told you to proceed, state the plan once and continue.

### 4. Migrate

Create a self-contained skill folder under the chosen active category or library root:

```text
<category>/<skill-name>/
├── SKILL.md
├── agents/openai.yaml
├── scripts/             # only if needed
├── references/          # only if needed
├── config.example.json  # only if needed
├── config.json          # git-ignored, machine-specific
├── .gitignore           # when needed
└── README.md             # when setup is non-trivial
```

Scripts must resolve their own path and must not depend on the current working directory.

### 5. Verify

Perform the checks that match the migrated skill:

- inspect for stale absolute paths/secrets/source-repo coupling;
- execute vendored scripts from the migrated location;
- verify dependency isolation;
- confirm secrets/config are git-ignored;
- run a bounded end-to-end task when practical;
- run `available` and confirm the nested source is discovered under the expected skill name;
- run `doctor --agent all` and resolve duplicate names, unmanaged runtime entries, or stale retired mounts.

Do not report migration success when the migrated skill has not been exercised enough to prove that it works from its new location.

### 6. Mount the verified result

After verification, use the scoped CLI to mount it where it belongs:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user --agent codex
```

or:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  mount <skill> --project /path/to/repo --agent all
```

Do not automatically mount every new skill globally. Choose scope and runtime based on reuse.

## Retiring a skill

When the user intentionally retires a skill:

1. Unmount it from active runtime scopes first, or move it and immediately run `doctor --agent all` to identify stale mounts.
2. Move the canonical source folder under `retire_skills/`.
3. Run `available` and confirm it is absent.
4. Run `doctor --agent all` and remove any reported `RETIRED` mounts explicitly.

Retirement changes availability only; it does not delete source history.

## References

- `references/skill-management.md` — recursive discovery, runtime adapters, status classification, mount/unmount/adopt/list/available/doctor behavior, and safety rules.
- `references/case-study-daily-summary.md` — full migration example.
- `references/migration-recipes.md` — migration patterns for config, paths, state, and vendoring.
- `references/determinism-audit.md` — deciding when mechanical workflow steps should be compiled into scripts.
