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

For Codex-oriented usage, a typical file is:

```json
{
  "libraries": ["/Users/you/source/personal-agent-skills/skills"],
  "user_target": "/Users/you/.agents/skills",
  "targets": ["/Users/you/.agents/skills"]
}
```

`deploy.json` is git-ignored. Use absolute paths.

`libraries` points at directories whose immediate children are skill folders containing `SKILL.md`.

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
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py mount agent-state
```

This mounts into:

```text
<git-root>/.agents/skills/agent-state
```

Explicit project:

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount agent-state --project /path/to/repo
```

### Mount as a user skill

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py \
  mount skill-librarian --user
```

This targets `~/.agents/skills` unless `user_target` overrides it.

### Unmount

```bash
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py unmount agent-state
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

`doctor` checks missing libraries, duplicate skill names, broken/wrong links, real directories in managed targets, duplicate user/project mounts, malformed `SKILL.md` names, and project symlinks accidentally tracked by Git.

## Safety behavior

- The source library is never modified by `mount` or `unmount`.
- `mount` refuses to replace a real directory/file.
- `unmount` refuses to delete a real directory/file.
- An existing wrong link is repaired only with `--force`.
- Project mounts use absolute local paths, so they are normally **not committed** to the project repository.
- Avoid mounting the same skill name at both user and project scope.

## Legacy bulk deployment

The original bulk deployment interface remains available:

```bash
python3 deploy.py
python3 deploy.py --dry-run
python3 deploy.py --skill NAME
```

This is useful when one `deploy.json` intentionally defines a fixed set of runtime targets. For new Codex projects, prefer the explicit `mount`/`unmount` interface.

## Portable skill conventions

A self-contained skill normally looks like:

```text
<skill-name>/
├── SKILL.md
├── scripts/              # optional runnable code
├── references/           # optional docs loaded on demand
├── config.example.json   # optional committed config shape
├── config.json           # optional machine-specific values, git-ignored
├── .gitignore            # when needed
└── README.md              # when human setup is non-trivial
```

Keep secrets and machine-specific paths out of committed files. Put skill-owned persistent state under `~/.local/state/skills/<skill-name>/` (or configured `state_root`), not inside source repositories.

## Recommended ownership

For a personal setup:

```text
personal-agent-skills/       # Git source of truth for your skills
skill_manager/               # management/migration tool
~/.agents/skills/            # user-scoped links only
<repo>/.agents/skills/       # project-scoped links only
```

That keeps “what the skill is” in Git, while user/project directories only express where a skill is active.
