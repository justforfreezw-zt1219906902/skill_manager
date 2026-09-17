# skill-librarian

A small framework for maintaining **portable agent skills** with one source of truth and link-based deployment into Codex, Claude Code, and other runtimes.

The intended model is:

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

Runtime directories are deployment views. Managed skills are symlinks on macOS/Linux or directory junctions on Windows, so edits or `git pull` in the source library propagate without copied directories drifting apart.

## Requirements

- Python 3
- Git for project-scoped mounts and Git-backed imports
- `uv` only when an individual migrated skill uses isolated Python dependencies

## Configuration

Copy the machine-specific example:

```bash
cp deploy.example.json deploy.json
```

A typical personal configuration is:

```json
{
  "libraries": ["/Users/you/source/personal-agent-skills"],
  "ignored_directories": ["retire_skills"],
  "runtimes": {
    "codex": {
      "enabled": true
    },
    "claude-code": {
      "enabled": true
    }
  },
  "targets": [
    "/Users/you/.agents/skills",
    "/Users/you/.claude/skills"
  ]
}
```

`deploy.json` is git-ignored. `libraries` should use absolute paths. Runtime paths have safe defaults and normally do not need to be configured:

| Runtime | User scope | Project scope |
| --- | --- | --- |
| Codex | `~/.agents/skills` | `<repo>/.agents/skills` |
| Claude Code | `~/.claude/skills` | `<repo>/.claude/skills` |

Optional per-runtime overrides are supported:

```json
{
  "runtimes": {
    "codex": {
      "enabled": true,
      "user_target": "/custom/codex/skills",
      "project_target": ".agents/skills"
    }
  }
}
```

`project_target` must be relative to the Git root. The old top-level `user_target` still overrides the Codex user target for backward compatibility.

`libraries` points at source-library roots. Discovery is recursive, so both flat and grouped layouts work:

```text
personal-agent-skills/
├── flat-skill/
│   └── SKILL.md
└── 3d_reconstruction_skills/
    └── reconstruction-geometry/
        └── SKILL.md
```

A directory containing `SKILL.md` is treated as a skill boundary and is not searched below. Hidden directories are skipped. `retire_skills` is ignored by default; use `ignored_directories` to add or replace ignored grouping-directory names.

## Scoped skill management

The scoped CLI is the preferred interface:

```bash
python3 skill-librarian/scripts/skill_librarian.py available
python3 skill-librarian/scripts/skill_librarian.py list
python3 skill-librarian/scripts/skill_librarian.py doctor
```

For compatibility with v0.2, runtime-scoped commands target **Codex by default**. Select another runtime with `--agent` / `-a`, repeat it for multiple runtimes, or use `--agent all`. `adopt` is intentionally single-runtime because it takes ownership of one concrete runtime entry. `import` is not runtime-scoped: it creates a canonical asset only.

### Mount to a project

From inside the target Git repo, Codex remains the default:

```bash
python3 skill-librarian/scripts/skill_librarian.py mount reconstruction-geometry
```

This mounts into:

```text
<git-root>/.agents/skills/reconstruction-geometry
```

Mount to Claude Code instead:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --agent claude-code
```

Mount to both runtimes:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --agent codex --agent claude-code
```

or:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --agent all
```

Explicit project path:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --project /path/to/repo --agent all
```

The category path in the source library does not appear in the runtime mount. A source at `3d_reconstruction_skills/reconstruction-geometry` mounts using only the skill basename.

Successful project-scoped `mount` and `adopt` operations automatically register the Git root for global inventory. Existing project mounts created before this feature need a one-time `project add` migration; the mount itself does not need to be recreated.

### Mount as a user skill

Codex user scope:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --user
```

Both supported user scopes:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --user --agent all
```

### Unmount

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  unmount reconstruction-geometry --user --agent codex
```

`unmount` removes links/junctions only. It refuses to delete a real directory or file. Multi-runtime mount/unmount operations preflight targets first so a blocked runtime does not silently leave a partial operation.

## Adopt unmanaged runtime skills

Use `adopt` when Codex, Claude Code, `npx skills`, or another tool has placed a skill directly in a runtime directory and `list`/`doctor` reports it as `UNMANAGED`.

Codex user scope:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  adopt downloaded-skill --user --agent codex
```

Project scope:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  adopt downloaded-skill --project /path/to/repo --agent claude-code
```

The default destination category is `imported/` inside the canonical library:

```text
personal-agent-skills/
└── imported/
    └── downloaded-skill/
        └── SKILL.md
```

Choose another category when the skill already has a durable classification:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  adopt postgres-review --user --category database_skills
```

If more than one external canonical library is configured, choose one explicitly:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  adopt postgres-review --user --library /absolute/path/to/personal-agent-skills
```

Preview without changing anything:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  adopt downloaded-skill --user --dry-run
```

`adopt` accepts exactly one runtime entry and only when its current status is `UNMANAGED`. It validates the runtime basename, `SKILL.md` name, destination category, configured library ownership, and internal links before mutating anything. The copy is staged and revalidated first; only then is the unmanaged runtime entry replaced with a managed link. If creating the managed link or final verification fails, the original runtime entry and canonical destination are rolled back.

If the unmanaged runtime entry is itself a symlink to an external skill source, `adopt` copies that source into the canonical library, replaces only the runtime link, and leaves the external source untouched.

For portability, `adopt` refuses broken/external internal links, absolute internal symlinks, and junction/reparse-point dependencies inside the skill tree. Clean or vendor those dependencies before adopting.

## Import external skills with provenance

Use `import` when the skill is **not already an unmanaged runtime install** and you want to turn an external/local source into a reviewed canonical asset first.

GitHub shorthand:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  import vercel-labs/agent-skills --skill frontend-design
```

Repository URL with an explicit revision:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  import https://github.com/acme/skills --skill postgres-review --ref v1.2.0
```

A common GitHub tree/blob URL can also identify the skill path. For branch names containing `/`, prefer a repository-root URL plus `--ref` because path/ref boundaries in tree URLs are ambiguous without GitHub API resolution.

Local source:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  import /path/to/source-repo --skill postgres-review --category database_skills
```

Preview acquisition and validation without writing:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  import vercel-labs/agent-skills --skill frontend-design --dry-run
```

`import` deliberately **does not mount the skill**. After review, activate it explicitly:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  mount frontend-design --user --agent codex
```

The imported skill receives `.skill-source.json`. Schema version 1 records:

```json
{
  "schema_version": 1,
  "type": "git",
  "source": "https://github.com/acme/skills.git",
  "source_path": "skills/postgres-review",
  "ref": "v1.2.0",
  "revision": "<exact-git-sha>",
  "dirty": false,
  "skill": "postgres-review",
  "acquired_via": "git",
  "imported_at": "<UTC timestamp>"
}
```

Provenance is intentionally sanitized: URL credentials, query strings, and fragments are removed; machine-local `file://` paths are not persisted. A plain local non-Git source records `source: null`. A local Git working tree records its exact current revision plus `dirty: true` when local changes/untracked files are part of the imported snapshot.

Import currently accepts existing local directories, GitHub `owner/repo`, GitHub repository/tree/blob URLs, and ordinary Git URLs. GitHub shorthand uses HTTPS; for private repositories where HTTPS Git credentials are not configured, pass an authenticated Git/SSH URL explicitly.

Safety checks happen before canonical ownership changes. Import rejects duplicate canonical names, hidden/ignored/escaping destination categories, non-portable links, `.env`-style secret files, and common machine-local dependency/cache directories such as `.venv` and `node_modules`. Nested VCS metadata such as `.git` is stripped from the canonical copy. Incoming `.skill-source.json` is not trusted; the importer writes fresh provenance after acquisition.

This acquisition layer is intentionally independent of `npx skills add`: external tools may still be used for discovery, but `skill_manager import` avoids installing into runtime directories while establishing canonical ownership.

## Runtime inspection and unmanaged detection

`list` reports the runtime, scope, skill name, status, and resolved target:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --agent all
```

For automation, use stable JSON output:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --agent all --json
```

The JSON object has a `mounts` array. Every row contains:

```text
runtime
scope
name
status
path
target
```

Current status values are:

- `MANAGED` - the runtime entry points at the canonical active source.
- `UNMANAGED` - a real directory/file or external link exists in a managed runtime directory but is not owned by the canonical libraries.
- `BROKEN_LINK` - the entry is a broken link outside the managed libraries.
- `WRONG_LINK` - the skill name exists in the canonical library but the runtime link points elsewhere.
- `RETIRED` - the runtime still points into an ignored/retired source subtree.
- `MISSING_SOURCE` - the runtime link points into a managed library location whose source no longer exists.

This is intentionally strict: a skill installed directly by Codex, Claude Code, `npx skills`, or another tool remains **unmanaged** until you explicitly run `adopt` or remove it.

## Global runtime inventory

`--global` is an aggregate inventory view, not a third scope. It combines runtime user scope with every explicitly registered project scope.

The Python script and executable launcher expose the same commands:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --global
./bin/skill-librarian list --global
```

Register project roots explicitly:

```bash
python3 skill-librarian/scripts/skill_librarian.py project add /path/to/repo
python3 skill-librarian/scripts/skill_librarian.py projects
python3 skill-librarian/scripts/skill_librarian.py project remove /path/to/repo
```

For a project that already had a project-scope mount before the registry feature, register it once from that Git repository:

```bash
cd /path/to/existing/project
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py project add .
```

For example, if `reconstruction-geometry` is already mounted in that project's `.agents/skills/`, registering the project is enough; do not remount the skill.

Then the global view can show both scopes together:

```text
RUNTIME       SCOPE    PROJECT                     SKILL                    STATUS
codex         user     -                           using-agent-skills       MANAGED
codex         project  ~/path/to/project           reconstruction-geometry MANAGED
```

The registry is machine-local at `~/.config/skill-librarian/projects.json` on macOS/Linux by default (or under `$XDG_CONFIG_HOME` when configured). It stores explicit Git roots and never recursively scans the whole home directory. Use `--agent all` to inspect all enabled runtimes and `--json` for machine-readable output.

See `docs/global-runtime-inventory.md` for details.

## Doctor

```bash
python3 skill-librarian/scripts/skill_librarian.py doctor --agent all
```

`doctor` checks:

- missing configured libraries;
- malformed or mismatched `SKILL.md` names;
- duplicate active skill names across source paths;
- unmanaged runtime entries;
- broken, wrong, retired, or missing-source links;
- duplicate user/project mounts within the same runtime;
- project mounts accidentally tracked by Git.

The same skill mounted into Codex and Claude Code is expected and is **not** treated as a duplicate. A duplicate means the same skill is simultaneously mounted at user and project scope inside one runtime.

## Safety behavior

- The source library is never modified by `mount` or `unmount`.
- `mount` refuses to replace unmanaged real directories/files.
- `unmount` refuses to delete unmanaged real directories/files.
- `adopt` is the explicit ownership transition from `UNMANAGED` runtime state to a canonical source plus managed runtime link.
- `adopt` never overwrites an existing canonical skill or destination.
- `adopt` requires a configured external canonical library and never adopts into the `skill_manager` framework root.
- `import` creates a canonical asset only; runtime activation remains a separate `mount` decision.
- `import` never overwrites an existing canonical skill and records sanitized provenance for later diff/update work.
- `adopt` and `import` reject path traversal, hidden/ignored destination categories, and non-portable internal links.
- An existing wrong link is repaired only with `--force`.
- Skills below ignored directories such as `retire_skills` are not available for mounting.
- `doctor` fails when a runtime still points into an ignored/retired source subtree.
- Project mounts use absolute local links and are normally **not committed** to the project repository.
- Avoid mounting the same skill at both user and project scope for one runtime.

## Legacy bulk deployment

The original bulk deployment interface remains available:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill NAME
```

Legacy deployment still uses the `targets` array and the same recursive source discovery/ignore rules. New workflows should use `import`/`adopt` for ownership and scoped `mount`/`unmount` plus runtime adapters for activation.

## Recommended ownership

For a personal setup:

```text
personal-agent-skills/                     # Git source of truth
├── imported/                              # default home for adopted/imported skills
├── 3d_reconstruction_skills/
│   └── reconstruction-geometry/
└── retire_skills/                         # ignored by discovery
    └── agent-state/

skill_manager/                             # control plane
~/.agents/skills/                          # Codex user links only
~/.claude/skills/                          # Claude Code user links only
<repo>/.agents/skills/                     # Codex project links only
<repo>/.claude/skills/                     # Claude Code project links only
```

That keeps **what the skill is and where it came from** in Git while runtime directories express only **where an active skill is mounted**.

## Roadmap

```text
v0.3  runtime adapters + unmanaged detection
v0.4  adopt unmanaged runtime skills into the canonical library
v0.5  import external skills + provenance metadata
v0.6  upstream diff / update using provenance
v0.7  registered-project global runtime inventory
```
