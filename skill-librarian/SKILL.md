---
name: skill-librarian
description: Manage a Git-backed central agent-skill library and make skills portable across Codex, Claude Code, and other runtimes. Use when the user wants to add/migrate a skill into the library, list available skills, mount or unmount a skill at user scope or project scope, inspect installed/mounted skills, repair links, or diagnose skill-library problems. Supports Codex user skills at ~/.agents/skills and project skills at <repo>/.agents/skills using symlinks/junctions while keeping the source skill library as the source of truth.
---

# Skill Librarian

Treat the central skill library as the source of truth. Runtime skill directories are deployment views made of links, not independent copies.

Use this model:

```text
central skill Git repo
        |
        +--> ~/.agents/skills/       user scope
        |
        +--> <repo>/.agents/skills/  project scope
```

Keep normal skill edits in the central source library. Do not edit mounted copies as if they were independent sources.

## Configuration

`config.json` in this skill is machine-specific and git-ignored. Copy `config.example.json` when needed.

Supported keys:

- `skill_library_path`: absolute path to the user's central skill library.
- `state_root`: absolute path for skill-owned persistent state used by migration workflows.

The framework-level `deploy.json` may additionally contain:

```json
{
  "libraries": ["/absolute/path/to/personal-agent-skills/skills"],
  "user_target": "/Users/<username>/.agents/skills",
  "targets": ["/Users/<username>/.agents/skills"]
}
```

`user_target` is preferred for scoped Codex user mounts. Existing `targets` remains supported for the legacy bulk-deploy flow.

Use absolute paths in committed examples and machine config. Never put secrets in committed files.

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

This targets:

```text
~/.agents/skills/<skill>
```

unless `user_target` overrides it.

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

### List

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" list
python "$SKILL_DIR/scripts/skill_librarian.py" list --user
python "$SKILL_DIR/scripts/skill_librarian.py" list --project /path/to/repo
```

With no scope, `list` inspects user scope and the current Git repo when available.

### Available

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
```

This lists canonical source skills discovered from the framework repo and configured libraries.

### Doctor

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" doctor
```

Use it after changing libraries, moving repos, or mounting/unmounting skills. It checks for:

- missing configured libraries;
- malformed or mismatched `SKILL.md` names;
- duplicate skill names across source libraries;
- broken or wrong links;
- real files/directories where managed links are expected;
- the same skill mounted at both user and project scope;
- project mounts accidentally tracked by Git.

Read `references/skill-management.md` for detailed scope and safety behavior.

## Safety rules for mount management

- Treat source skill folders as immutable from mount/unmount operations.
- Never copy a skill merely to deploy it unless symlinks/junctions are impossible and the user explicitly asks for a copy.
- Never replace a real target directory automatically.
- Only use `--force` to repair an existing link that points at the wrong source, after confirming that repair is intended.
- Project mounts are normally local machine state because they point to absolute source paths. Do not commit those links unless the project explicitly wants that machine-specific behavior.
- If a skill is mounted at user scope, normally do not also mount the same name at project scope.

## Legacy bulk deployment

The root `deploy.py` remains supported for backward compatibility:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill <name>
```

Prefer scoped `mount`/`unmount` commands for new Codex workflows because they make user-vs-project ownership explicit.

## Migrating a skill into the library

Use the migration workflow only when the source skill is not already a clean, self-contained library skill.

### 1. Locate and guard

Take the source skill path from the user. Read its `SKILL.md`. Determine the intended skill name. If a skill with that name already exists in the central library, stop rather than overwrite it.

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

Before changing an existing source skill, summarize the dependency audit, proposed migration, configuration keys, and any non-obvious decisions. If the user already explicitly told you to proceed, state the plan once and continue.

### 4. Migrate

Create a self-contained library folder:

```text
<skill-name>/
├── SKILL.md
├── scripts/          # only if needed
├── references/       # only if needed
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
- run a bounded end-to-end task when practical.

Do not report migration success when the migrated skill has not been exercised enough to prove that it works from its new location.

### 6. Mount the migrated result

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

## References

- `references/skill-management.md` — mount/unmount/list/available/doctor behavior and safety rules.
- `references/case-study-daily-summary.md` — full migration example.
- `references/migration-recipes.md` — migration patterns for config, paths, state, and vendoring.
- `references/determinism-audit.md` — deciding when mechanical workflow steps should be compiled into scripts.
