# Skill management commands

Use the bundled CLI at `scripts/skill_librarian.py` for runtime mount, inspection, and adoption operations. The source skill library is the source of truth; runtime directories are link-only deployment views once a skill is managed.

## Commands

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill>
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --project /absolute/path/to/repo
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --agent claude-code
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --agent all
python "$SKILL_DIR/scripts/skill_librarian.py" unmount <skill>
python "$SKILL_DIR/scripts/skill_librarian.py" adopt <skill> --user --agent codex
python "$SKILL_DIR/scripts/skill_librarian.py" adopt <skill> --project /absolute/path/to/repo --agent claude-code
python "$SKILL_DIR/scripts/skill_librarian.py" list --agent all
python "$SKILL_DIR/scripts/skill_librarian.py" list --agent all --json
python "$SKILL_DIR/scripts/skill_librarian.py" doctor --agent all
```

## Runtime adapters

Built-in runtimes:

| Runtime | User scope | Project scope |
| --- | --- | --- |
| `codex` | `~/.agents/skills` | `<repo>/.agents/skills` |
| `claude-code` | `~/.claude/skills` | `<repo>/.claude/skills` |

Codex is the default when `--agent` is omitted. `--agent` is repeatable, and `--agent all` selects every enabled runtime. `claude` is accepted as an alias for `claude-code`.

`adopt` is the exception: it intentionally accepts exactly one runtime because it takes ownership of one concrete runtime entry. Do not use `--agent all` with `adopt` when multiple runtimes are enabled.

Optional runtime overrides live in `deploy.json`:

```json
{
  "runtimes": {
    "codex": {
      "enabled": true,
      "user_target": "/absolute/custom/codex/skills",
      "project_target": ".agents/skills"
    },
    "claude-code": {
      "enabled": true
    }
  }
}
```

`user_target` must be absolute (or `~`-based). `project_target` must be a relative subdirectory that stays inside the Git root; it may not be `.` or contain `..`. Unknown runtime names or runtime keys are configuration errors.

Selected runtime/scope targets must resolve to distinct physical directories. If two adapters are configured to the same directory, the operation fails before mutating anything.

## Scope rules

- `mount <skill>` defaults to the current Git repository and Codex runtime.
- `mount <skill> --user` defaults to Codex user scope.
- `adopt <skill>` also defaults to the current Git repository and Codex runtime.
- `adopt <skill> --user` takes ownership of one Codex user-scope runtime entry unless another single runtime is selected explicitly.
- `--project REPO` always resolves the Git root before mounting or adopting.
- `list` and `doctor` with no scope inspect user scope plus the current Git repo when available.
- Source grouping folders never appear in the runtime mount path.
- The same skill may be mounted into different runtimes; that is expected.
- The same skill mounted at user and project scope inside one runtime is treated as a duplicate/conflict.

## Library discovery

The CLI discovers skills from:

1. the skill-librarian framework repo itself;
2. `libraries` in the framework repo's git-ignored `deploy.json`;
3. `skill_library_path` in this skill's git-ignored `config.json`, when present.

Each configured library is a root, not necessarily a directory whose immediate children are skills. Discovery walks grouping folders recursively. A directory containing `SKILL.md` is a skill boundary: it is registered by basename and the scanner does not recurse below it.

Example:

```text
personal-agent-skills/
├── imported/
│   └── adopted-skill/
│       └── SKILL.md
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
│       └── SKILL.md
└── retire_skills/
    └── agent-state/
        └── SKILL.md
```

`adopted-skill` and `reconstruction-geometry` are discovered. `agent-state` is not, because `retire_skills` is ignored by default.

Hidden directories are always skipped. Grouping-directory symlinks are not traversed. A symlink that directly represents a skill directory containing `SKILL.md` remains supported for backward compatibility.

Configure ignored group names with:

```json
{
  "ignored_directories": ["retire_skills", "archive"]
}
```

If the key is absent, the default is `["retire_skills"]`. Framework `deploy.json` takes precedence; standalone/deployed `skill-librarian` may use the same key in its local `config.json`.

If two active source paths provide the same skill basename, the first configured library wins and `doctor` reports the collision.

## Runtime status model

`list` and `doctor` classify each runtime entry with one of these statuses:

- `MANAGED`: the runtime entry points at the canonical active source.
- `UNMANAGED`: a real directory/file or external link exists in a managed runtime directory but is not owned by the canonical libraries.
- `BROKEN_LINK`: the runtime entry is a broken link outside the managed libraries.
- `WRONG_LINK`: the skill name exists in the canonical library, but this runtime link points elsewhere.
- `RETIRED`: the runtime still points into an ignored/retired source subtree.
- `MISSING_SOURCE`: the runtime points into a managed library location whose source no longer exists.

`list --json` emits:

```json
{
  "mounts": [
    {
      "runtime": "codex",
      "scope": "user",
      "name": "reconstruction-geometry",
      "status": "MANAGED",
      "path": "/Users/me/.agents/skills/reconstruction-geometry",
      "target": "/Users/me/source/personal-agent-skills/3d_reconstruction_skills/reconstruction-geometry"
    }
  ]
}
```

A skill installed directly by Codex, Claude Code, `npx skills`, or another tool is `UNMANAGED` until it is explicitly adopted or removed. Never silently reinterpret an unmanaged runtime directory as canonical source.

## Adopt unmanaged entries

`adopt` is the explicit transition from unmanaged runtime state to a canonical library asset plus a managed runtime link.

Typical user-scope command:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt downloaded-skill --user --agent codex
```

The default canonical destination is:

```text
<configured-library>/imported/downloaded-skill/
```

Choose a durable category when appropriate:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt postgres-review --user --category database_skills
```

If more than one external canonical library is configured, select one explicitly:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt postgres-review --user --library /absolute/path/to/personal-agent-skills
```

Preview first when useful:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  adopt downloaded-skill --user --dry-run
```

Adoption preconditions:

- the runtime entry must exist and classify as exactly `UNMANAGED`;
- the skill argument must be a simple basename, not a path;
- no active canonical skill with that basename may already exist;
- the destination library must be a configured external canonical library, never the `skill_manager` framework root;
- the destination category must be relative, non-hidden, non-ignored, and stay inside the library;
- `SKILL.md` must exist and its frontmatter `name` must equal the runtime basename;
- the canonical library and selected runtime target must not overlap.

Portability validation intentionally rejects:

- broken links inside the skill tree;
- internal links that resolve outside the skill tree;
- absolute internal symlinks, even if they currently point back into the source tree, because copying them would preserve the old absolute path;
- junction/reparse-point dependencies inside the skill tree.

The mutation sequence is transactional at the filesystem level as far as practical:

```text
UNMANAGED runtime entry
        |
        +--> copy to hidden staging dir inside canonical library
        +--> validate staged copy
        +--> move staged copy to final canonical destination
        +--> move original runtime entry to temporary backup
        +--> create managed runtime link to canonical destination
        +--> remove backup
        +--> verify final status == MANAGED
```

If managed-link creation fails, the command attempts to restore the original runtime entry and remove the newly created canonical destination. A rollback failure is reported explicitly rather than hidden.

When the unmanaged runtime entry is itself an external symlink, the target contents are copied into the canonical library, the runtime link is replaced with the managed link, and the external source target is left untouched.

## Safety rules

- Mounts are symlinks on macOS/Linux and directory junctions on Windows.
- `unmount` only removes a link/junction; it refuses to delete a real file or directory.
- `mount` refuses to overwrite an unmanaged real path.
- `adopt` is the only first-class command that intentionally takes ownership of an unmanaged runtime entry, and it does so only after validation and staging.
- `adopt` never overwrites an existing canonical skill/destination and never adopts into an ignored/retired category.
- Multi-runtime mount/unmount operations preflight every selected target before mutation.
- If a link exists but points at the wrong active source, use `mount <skill> ... --force` only after confirming that repair is intended.
- A skill below an ignored directory cannot be mounted through `mount` because it is absent from active discovery.
- `doctor` reports mounted links into ignored/retired library subtrees as failures so retirement cannot silently leave an active runtime skill behind.
- `doctor` also flags unmanaged entries, broken links, wrong links, missing managed sources, duplicate source names, user/project duplicate mounts inside one runtime, malformed `SKILL.md` names, missing configured libraries, invalid runtime config, runtime target collisions, and project mounts tracked by Git.
- Project mounts are machine-specific absolute links; normally do not commit them.

## Recommended model

```text
personal-agent-skills Git repo             source of truth
        |
        +-- imported/                      default home for adopted skills
        +-- active category folders        recursively discovered
        +-- retire_skills/                 ignored
        |
        +--> skill_manager                 control plane
                |
                +--> CodexAdapter
                |      +--> ~/.agents/skills/
                |      +--> <repo>/.agents/skills/
                |
                +--> ClaudeCodeAdapter
                       +--> ~/.claude/skills/
                       +--> <repo>/.claude/skills/
```

Keep edits in the source library. Never edit managed runtime links as though they were independent sources. Use `adopt` for explicit unmanaged-to-managed ownership transitions.