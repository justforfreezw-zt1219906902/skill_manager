# skill-librarian

A small framework for maintaining **portable agent skills** with one source of truth and link-based deployment into Codex, Claude Code, and other runtimes.

The intended model is:

```text
central skill Git repo
        |
        +--> ~/.agents/skills/       Codex user scope
        |
        +--> <repo>/.agents/skills/  Codex project scope
```

The central library is the source of truth. Runtime directories should normally contain symlinks on macOS/Linux or directory junctions on Windows, so edits or `git pull` in the library propagate without copies drifting apart.

## Requirements

- Python 3
- Git for project-scoped mounts
- `uv` only when an individual migrated skill uses isolated Python dependencies

## Configuration

Copy the machine-specific example:

```bash
cp deploy.example.json deploy.json
```

For Codex-oriented usage with a grouped personal library, a typical file is:

```json
{
  "libraries": ["/Users/you/source/personal-agent-skills"],
  "ignored_directories": ["retire_skills"],
  "user_target": "/Users/you/.agents/skills",
  "targets": ["/Users/you/.agents/skills"]
}
```

`deploy.json` is git-ignored. Use absolute paths.

`libraries` points at source-library roots. Discovery is recursive, so both of these layouts work:

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

The preferred interface for new Codex workflows is:

```bash
python3 skill-librarian/scripts/skill_librarian.py available
python3 skill-librarian/scripts/skill_librarian.py list
python3 skill-librarian/scripts/skill_librarian.py doctor
```

### Mount to the current project

From any directory inside a Git repo:

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py mount reconstruction-geometry
```

This mounts into:

```text
<git-root>/.agents/skills/reconstruction-geometry
```

Explicit project:

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount reconstruction-geometry --project /path/to/repo
```

The category path in the source library does not appear in the runtime mount. A source at `3d_reconstruction_skills/reconstruction-geometry` still mounts as `.agents/skills/reconstruction-geometry`.

### Mount as a user skill

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount skill-librarian --user
```

This targets `~/.agents/skills` unless `user_target` overrides it.

### Unmount

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py unmount reconstruction-geometry
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py unmount skill-librarian --user
```

Unmounting removes links only. It refuses to delete a real directory or file.

### Inspect mounts

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py list
```

Without an explicit scope, `list` shows user scope and the current Git project's scope when available.

### Diagnose problems

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py doctor
```

`doctor` checks missing libraries, duplicate skill names, broken/wrong links, real directories in managed targets, duplicate user/project mounts, malformed `SKILL.md` names, project symlinks accidentally tracked by Git, and links that still point into ignored/retired source subtrees.

## Safety behavior

- The source library is never modified by `mount` or `unmount`.
- `mount` refuses to replace a real directory/file.
- `unmount` refuses to delete a real directory/file.
- An existing wrong link is repaired only with `--force`.
- Skills below ignored directories such as `retire_skills` are not available for mounting.
- `doctor` fails when a runtime mount still points into an ignored/retired subtree.
- Project mounts use absolute local paths, so they are normally **not committed** to the project repository.
- Avoid mounting the same skill name at both user and project scope.

## Legacy bulk deployment

The original bulk deployment interface remains available:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill NAME
```

Legacy deployment uses the same recursive source discovery and ignore rules.

## Recommended ownership

For a personal setup:

```text
personal-agent-skills/                     # Git source of truth
├── 3d_reconstruction_skills/              # optional category/group
│   └── reconstruction-geometry/
└── retire_skills/                         # ignored by discovery
    └── agent-state/

skill_manager/                             # management/migration tool
~/.agents/skills/                          # user-scoped links only
<repo>/.agents/skills/                     # project-scoped links only
```

That keeps “what the skill is” in Git, while user/project directories only express where an active skill is mounted.
