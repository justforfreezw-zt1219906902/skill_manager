# Skill management commands

Use the bundled CLI at `scripts/skill_librarian.py` for acquisition, canonical ownership, runtime mount, inspection, and adoption operations. The source skill library is the source of truth; runtime directories are link-only deployment views once a skill is managed.

## Commands

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
python "$SKILL_DIR/scripts/skill_librarian.py" import owner/repo --skill <skill>
python "$SKILL_DIR/scripts/skill_librarian.py" import https://github.com/owner/repo --skill <skill> --ref <ref>
python "$SKILL_DIR/scripts/skill_librarian.py" import /local/source --skill <skill> --category <category>
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

## Ownership layers

Treat acquisition, canonical ownership, and runtime activation as separate layers:

```text
external/local source
       |
       | import
       v
canonical library (+ .skill-source.json)
       |
       | mount
       v
runtime managed link
```

An alternative entry point exists when the runtime already contains an unmanaged copy:

```text
UNMANAGED runtime entry
       |
       | adopt
       v
canonical library + managed runtime link
```

`import` never mounts. `mount` never acquires. `adopt` is the explicit bridge for an already-installed unmanaged runtime entry.

## Import source acquisition

`import` accepts:

- an existing local directory;
- GitHub shorthand `owner/repo`;
- a GitHub repository URL;
- common GitHub `/tree/<ref>/<path>` or `/blob/<ref>/.../SKILL.md` URLs;
- normal Git URLs (`https`, `ssh`, `git`, `file`, or scp-style `git@host:path`).

GitHub shorthand is normalized to HTTPS Git. For private repositories where HTTPS credentials are not configured, pass an SSH/authenticated Git URL explicitly.

A common GitHub tree/blob URL supplies both a ref and a path hint. Branch names containing `/` are ambiguous in a URL path; use a repository-root URL plus `--ref` for those refs.

Remote Git sources are cloned to a temporary directory. The exact checked-out commit SHA is recorded. `--ref` may select a branch, tag, or revision for remote Git acquisition.

Local sources are read in place. When they live in a Git repository, provenance records the current Git revision and whether the working tree is dirty. This means `dirty: true` explicitly signals that the imported snapshot may contain content not represented by the recorded commit alone.

Skill selection is by `SKILL.md` frontmatter `name`. Discovery respects skill boundaries: once a directory contains `SKILL.md`, import discovery does not search below that skill for another requested skill. Ambiguous duplicate matches are rejected.

## Import canonicalization

Typical import:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  import vercel-labs/agent-skills --skill frontend-design
```

Choose an exact source revision:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  import https://github.com/acme/skills --skill postgres-review --ref v1.2.0
```

Choose another configured library/category:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  import /path/to/source --skill postgres-review \
  --library /absolute/path/to/personal-agent-skills \
  --category database_skills
```

Preview acquisition and validation without canonical writes:

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" \
  import vercel-labs/agent-skills --skill frontend-design --dry-run
```

Import preconditions and safety rules:

- the requested skill name must be a simple basename;
- no active canonical skill with that name may already exist;
- the destination must be inside a configured external canonical library, never the framework root;
- destination category ancestry may not traverse hidden/ignored directories, links/junctions, or an existing skill boundary;
- `SKILL.md` frontmatter name must match the requested skill;
- broken/external internal links, absolute internal symlinks, and junction/reparse-point dependencies are rejected for portability;
- `.env` and `.env.*` secret-like files are rejected except explicit examples/templates such as `.env.example`, `.env.sample`, and `.env.template`;
- common transient dependency/cache directories such as `.venv`, `node_modules`, `__pycache__`, `.tox`, `.mypy_cache`, and `.pytest_cache` are rejected;
- nested `.git`, `.hg`, and `.svn` metadata is stripped from the canonical copy;
- an incoming `.skill-source.json` is untrusted and replaced with provenance generated from the acquisition step;
- canonical discovery is verified after the staged copy is moved into place; failure removes the new destination.

After import succeeds, inspect/verify the canonical skill and call `mount` separately where activation is desired.

## Provenance schema

Imported skills contain `.skill-source.json`. Schema version 1 fields:

```json
{
  "schema_version": 1,
  "type": "git",
  "source": "https://github.com/acme/skills.git",
  "source_path": "skills/postgres-review",
  "ref": "v1.2.0",
  "revision": "0123456789abcdef...",
  "dirty": false,
  "skill": "postgres-review",
  "acquired_via": "git",
  "imported_at": "2026-09-16T12:00:00Z"
}
```

Semantics:

- `schema_version`: provenance record format version.
- `type`: currently `git` or `local`.
- `source`: portable/sanitized source identifier when available, otherwise `null`.
- `source_path`: path to the skill within the acquired repository/source root.
- `ref`: explicitly requested remote Git ref when present.
- `revision`: exact Git commit at acquisition time when known.
- `dirty`: local Git working tree state; `true` means the snapshot may differ from `revision`. Remote clones are `false`. Plain non-Git local sources use `null`.
- `skill`: requested/canonical skill name.
- `acquired_via`: `git` or `local`.
- `imported_at`: UTC timestamp.

Provenance privacy rules:

- remove embedded HTTP(S) credentials;
- remove URL query strings and fragments;
- do not persist machine-specific `file://` paths;
- plain local non-Git paths are not committed as `source`;
- SSH usernames may be retained as transport identity, but passwords/tokens must never be embedded.

This metadata is the foundation for later upstream `diff`/`update` work. It is not itself permission to overwrite local modifications.

## Runtime adapters

Built-in runtimes:

| Runtime | User scope | Project scope |
| --- | --- | --- |
| `codex` | `~/.agents/skills` | `<repo>/.agents/skills` |
| `claude-code` | `~/.claude/skills` | `<repo>/.claude/skills` |

Codex is the default when `--agent` is omitted. `--agent` is repeatable, and `--agent all` selects every enabled runtime. `claude` is accepted as an alias for `claude-code`.

`adopt` is the exception: it intentionally accepts exactly one runtime because it takes ownership of one concrete runtime entry. `import` has no runtime selector because it does not mount.

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
│   └── owned-skill/
│       └── SKILL.md
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
│       └── SKILL.md
└── retire_skills/
    └── agent-state/
        └── SKILL.md
```

`owned-skill` and `reconstruction-geometry` are discovered. `agent-state` is not, because `retire_skills` is ignored by default.

Hidden directories are always skipped. Grouping-directory symlinks are not traversed. A symlink that directly represents a skill directory containing `SKILL.md` remains supported for backward compatibility.

If two active source paths provide the same skill basename, the first configured library wins and `doctor` reports the collision.

## Runtime status model

`list` and `doctor` classify each runtime entry with one of these statuses:

- `MANAGED`: the runtime entry points at the canonical active source.
- `UNMANAGED`: a real directory/file or external link exists in a managed runtime directory but is not owned by the canonical libraries.
- `BROKEN_LINK`: the runtime entry is a broken link outside the managed libraries.
- `WRONG_LINK`: the skill name exists in the canonical library, but this runtime link points elsewhere.
- `RETIRED`: the runtime still points into an ignored/retired source subtree.
- `MISSING_SOURCE`: the runtime points into a managed library location whose source no longer exists.

`list --json` emits stable rows containing `runtime`, `scope`, `name`, `status`, `path`, and `target`.

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

Adoption preconditions:

- the runtime entry must exist and classify as exactly `UNMANAGED`;
- the skill argument must be a simple basename, not a path;
- no active canonical skill with that basename may already exist;
- the destination library must be a configured external canonical library, never the `skill_manager` framework root;
- the destination category must be relative, non-hidden, non-ignored, and stay inside the library;
- category ancestry may not cross an existing skill boundary or symlink/junction;
- `SKILL.md` must exist and its frontmatter `name` must equal the runtime basename;
- the canonical library and selected runtime target must not overlap.

Portability validation intentionally rejects broken links, links outside the skill tree, absolute internal symlinks, and junction/reparse-point dependencies.

The mutation sequence keeps the original backup until final verification:

```text
UNMANAGED runtime entry
        |
        +--> copy to hidden staging dir inside canonical library
        +--> validate staged copy
        +--> move staged copy to final canonical destination
        +--> verify canonical discovery
        +--> move original runtime entry to temporary backup
        +--> create managed runtime link to canonical destination
        +--> verify final status == MANAGED
        +--> remove backup
```

If link creation or final verification fails, the command attempts to restore the original runtime entry and remove the newly created canonical destination. Rollback problems are reported explicitly.

When the unmanaged runtime entry is itself an external symlink, the target contents are copied into the canonical library, the runtime link is replaced with the managed link, and the external source target is left untouched.

## Safety rules

- Mounts are symlinks on macOS/Linux and directory junctions on Windows.
- `import` establishes canonical ownership from an external/local source and never mounts automatically.
- `unmount` only removes a link/junction; it refuses to delete a real file or directory.
- `mount` refuses to overwrite an unmanaged real path.
- `adopt` is the only first-class command that intentionally takes ownership of an unmanaged runtime entry, and it does so only after validation and staging.
- `import` and `adopt` never overwrite an existing canonical skill/destination and never target an ignored/retired category.
- Multi-runtime mount/unmount operations preflight every selected target before mutation.
- If a link exists but points at the wrong active source, use `mount <skill> ... --force` only after confirming that repair is intended.
- `doctor` reports mounted links into ignored/retired library subtrees as failures so retirement cannot silently leave an active runtime skill behind.
- `doctor` also flags unmanaged entries, broken links, wrong links, missing managed sources, duplicate source names, user/project duplicate mounts inside one runtime, malformed `SKILL.md` names, missing configured libraries, invalid runtime config, runtime target collisions, and project mounts tracked by Git.
- Project mounts are machine-specific absolute links; normally do not commit them.

## Recommended model

```text
external discovery / Git sources
        |
        +--> skill_manager import
        |
personal-agent-skills Git repo             source of truth
        |
        +-- imported/                      default home for imported/adopted skills
        +-- active category folders        recursively discovered
        +-- retire_skills/                 ignored
        |
        +--> skill_manager control plane
                |
                +--> CodexAdapter
                |      +--> ~/.agents/skills/
                |      +--> <repo>/.agents/skills/
                |
                +--> ClaudeCodeAdapter
                       +--> ~/.claude/skills/
                       +--> <repo>/.claude/skills/
```

Keep edits in the source library. Never edit managed runtime links as though they were independent sources. Use `import` for clean external acquisition, `adopt` for explicit unmanaged-to-managed runtime ownership transitions, and `mount` for activation.
