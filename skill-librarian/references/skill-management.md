# Skill management commands

Use the bundled CLI at `scripts/skill_librarian.py` for all mount operations. The source skill library is the source of truth; user/project runtime directories are link-only views.

## Commands

```bash
python "$SKILL_DIR/scripts/skill_librarian.py" available
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill>
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --user
python "$SKILL_DIR/scripts/skill_librarian.py" mount <skill> --project /absolute/path/to/repo
python "$SKILL_DIR/scripts/skill_librarian.py" unmount <skill>
python "$SKILL_DIR/scripts/skill_librarian.py" list
python "$SKILL_DIR/scripts/skill_librarian.py" doctor
```

## Scope rules

- `mount <skill>` defaults to the current Git repository and creates `<repo>/.agents/skills/<skill>`.
- `mount <skill> --user` creates `~/.agents/skills/<skill>`.
- `--project REPO` always resolves the Git root before mounting.
- `list` and `doctor` with no scope inspect user scope plus the current Git repo when available.
- Source grouping folders never appear in the runtime mount path.

## Library discovery

The CLI discovers skills from:

1. the skill-librarian framework repo itself;
2. `libraries` in the framework repo's git-ignored `deploy.json`;
3. `skill_library_path` in this skill's git-ignored `config.json`, when present.

Each configured library is a **root**, not necessarily a directory whose immediate children are skills. Discovery walks grouping folders recursively. A directory containing `SKILL.md` is a skill boundary: it is registered by basename and the scanner does not recurse below it.

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

`reconstruction-geometry` is discovered. `agent-state` is not, because `retire_skills` is ignored by default.

Hidden directories are always skipped. Grouping-directory symlinks are not traversed. A symlink that directly represents a skill directory containing `SKILL.md` remains supported for backward compatibility.

Configure ignored group names with:

```json
{
  "ignored_directories": ["retire_skills", "archive"]
}
```

If the key is absent, the default is `["retire_skills"]`. Framework `deploy.json` takes precedence; standalone/deployed `skill-librarian` may use the same key in its local `config.json`.

If two active source paths provide the same skill basename, the first configured library wins and `doctor` reports the collision.

## Safety rules

- Mounts are symlinks on macOS/Linux and directory junctions on Windows.
- `unmount` only removes a link/junction; it refuses to delete a real file or directory.
- `mount` refuses to overwrite a real path.
- If a link exists but points at the wrong active source, use `mount <skill> ... --force` only after confirming that repair is intended.
- A skill below an ignored directory cannot be mounted through `mount` because it is absent from active discovery.
- `doctor` reports a mounted link into an ignored/retired library subtree as a failure so retirement cannot silently leave an active runtime skill behind.
- `doctor` also flags broken links, wrong links, duplicate source names, user/project duplicate mounts, malformed `SKILL.md` names, missing configured libraries, and project mounts tracked by Git.
- Project mounts are machine-specific absolute links; normally do not commit them.

## Recommended model

```text
personal-agent-skills Git repo             source of truth
        |
        +-- active category folders        recursively discovered
        +-- retire_skills/                 ignored
        |
        +--> ~/.agents/skills/             user-scoped links
        +--> <repo>/.agents/skills/        project-scoped links
```

Keep edits in the source library. Never edit mounted copies as though they were independent files.
