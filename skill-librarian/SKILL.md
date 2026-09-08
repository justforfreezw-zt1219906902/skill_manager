---
name: skill-librarian
description: Manage a Git-backed central agent-skill library and make skills portable across Codex, Claude Code, and other runtimes. Use when the user wants to add/migrate a skill into the library, organize skills into category folders, retire skills, list available skills, mount or unmount a skill at user scope or project scope, inspect installed/mounted skills, repair links, or diagnose skill-library problems. Supports recursive grouped source libraries, ignored retirement folders, Codex user skills at ~/.agents/skills, and project skills under a repository .agents/skills directory while keeping the source library authoritative.
---

# Skill Librarian

Treat the central skill library as the source of truth. Runtime skill directories are deployment views made of links, not independent copies.

Use this model:

```text
central skill Git repo
        |
        +-- category folders (optional)
        +-- retire_skills/ (ignored by default)
        |
        +--> ~/.agents/skills/       user scope
        +--> <repo>/.agents/skills/  project scope
```

Keep normal skill edits in the central source library. Do not edit mounted copies as if they were independent sources.

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

Category folder names are organizational only. A source at `3d_reconstruction_skills/reconstruction-geometry` mounts as `.agents/skills/reconstruction-geometry`.

## Configuration

`config.json` in this skill is machine-specific and git-ignored. Copy `config.example.json` when needed.

Supported keys:

- `skill_library_path`: absolute path to the user's central skill-library root.
- `ignored_directories`: source-directory basenames to prune during recursive discovery. Defaults to `["retire_skills"]`.
- `state_root`: absolute path for skill-owned persistent state used by migration workflows.

The framework-level `deploy.json` may additionally contain:

```json
{
  "libraries": ["/absolute/path/to/personal-agent-skills"],
  "ignored_directories": ["retire_skills"],
  "user_target": "/Users/<username>/.agents/skills",
  "targets": ["/Users/<username>/.agents/skills"]
}
```

Framework `deploy.json` takes precedence for `ignored_directories`; a standalone/deployed `skill-librarian` falls back to its local `config.json`.

Use absolute paths in machine config. Never put secrets in committed files.

## Skill management

Use the bundled deterministic CLI for link management. Do not hand-write `ln -s` when the CLI is available.

Set:

```bash
SKILL_DIR=<absolute path to the directory containing this SKILL.md>
```

Then use:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
python "$SKILL_DIR/scripts/skill_librarian.py" list
python "$SKILL_DIR/scripts/skill_librarian.py" doctor
```

### Mount a project skill

From inside the target Git repo:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill>
```

This defaults to:

```text
<git-root>/.agents/skills/<skill>
```

Or specify the repo explicitly:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --project /absolute/path/to/repo
```

### Mount a user skill

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user
```

This targets `~/.agents/skills/<skill>` unless `user_target` overrides it.

### Unmount

Project scope:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" unmount <skill>
```

User scope:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" unmount <skill> --user
```

`unmount` removes links/junctions only. If the target is a real directory or file, stop and report it; never delete it automatically.

### Available

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
```

List active canonical source skills only. Do not list skills below ignored directories such as `retire_skills`.

### Doctor

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" doctor
```

Use it after changing libraries, regrouping skills, retiring skills, moving repos, or mounting/unmounting skills. It checks for:

- missing configured libraries;
- malformed or mismatched `SKILL.md` names;
- duplicate active skill names across source paths;
- broken or wrong links;
- links that still point into ignored/retired source subtrees;
- real files/directories where managed links are expected;
- the same skill mounted at both user and project scope;
- project mounts accidentally tracked by Git.

Read `references/skill-management.md` for exact discovery, scope, retirement, and safety behavior.

## Safety rules for mount management

- Treat source skill folders as immutable from mount/unmount operations.
- Never copy a skill merely to deploy it unless symlinks/junctions are impossible and the user explicitly asks for a copy.
- Never replace a real target directory automatically.
- Only use `--force` to repair an existing link that points at the wrong active source, after confirming that repair is intended.
- Do not mount skills from ignored/retired source folders.
- If `doctor` reports a retired link, unmount it explicitly; do not silently relink it elsewhere.
- Project mounts are normally local machine state because they point to absolute source paths. Do not commit those links unless the project explicitly wants that machine-specific behavior.
- If a skill is mounted at user scope, normally do not also mount the same name at project scope.

## Legacy bulk deployment

The root `deploy.py` remains supported for backward compatibility:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill <name>
```

Legacy deployment uses the same recursive discovery and ignore rules. Prefer scoped `mount`/`unmount` commands for new Codex workflows because they make user-vs-project ownership explicit.

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
- run `doctor` and resolve duplicate names or stale retired mounts.

Do not report migration success when the migrated skill has not been exercised enough to prove that it works from its new location.

### 6. Mount the verified result

After verification, use the scoped CLI to mount it where it belongs:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user
```

or:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --project /path/to/repo
```

Do not automatically mount every new skill globally. Choose scope based on reuse:

- broadly reusable across projects -> user scope;
- relevant only to one repository/workflow -> project scope.

## Retiring a skill

When the user intentionally retires a skill:

1. Unmount it from user/project scopes first, or move it and immediately run `doctor` to identify stale mounts.
2. Move the canonical source folder under `retire_skills/`.
3. Run `available` and confirm it is absent.
4. Run `doctor` and remove any reported `retired-link` mounts explicitly.

Retirement changes availability only; it does not delete source history.

## References

- `references/skill-management.md` — recursive discovery, mount/unmount/list/available/doctor behavior and safety rules.
- `references/case-study-daily-summary.md` — full migration example.
- `references/migration-recipes.md` — migration patterns for config, paths, state, and vendoring.
- `references/determinism-audit.md` — deciding when mechanical workflow steps should be compiled into scripts.
