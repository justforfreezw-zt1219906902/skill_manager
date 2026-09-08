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

### Scope rules

- `mount <skill>` defaults to the current Git repository and creates `<repo>/.agents/skills/<skill>`.
- `mount <skill> --user` creates `~/.agents/skills/<skill>`.
- `--project REPO` always resolves the Git root before mounting.
- `list` and `doctor` with no scope inspect user scope plus the current Git repo when available.

### Safety rules

- Mounts are symlinks on macOS/Linux and directory junctions on Windows.
- `unmount` only removes a link/junction; it refuses to delete a real file or directory.
- `mount` refuses to overwrite a real path.
- If a link exists but points at the wrong source, use `mount <skill> ... --force` only after confirming that repair is intended.
- `doctor` flags broken links, wrong links, duplicate source names, user/project duplicate mounts, malformed `SKILL.md` names, missing configured libraries, and project mounts that are tracked by Git.
- Project mounts are machine-specific absolute links; normally do not commit them. If a team needs reproducible project skill selection, commit a manifest/profile and recreate the links locally instead.

## Library discovery

The CLI discovers skills from:

1. the skill-librarian framework repo itself;
2. `libraries` in the framework repo's git-ignored `deploy.json`;
3. `skill_library_path` in this skill's git-ignored `config.json`, when present.

A discovered skill is any immediate child folder containing `SKILL.md`. If two libraries provide the same skill name, the first library wins and `doctor` reports the collision.

## Recommended model

```text
personal-agent-skills Git repo       source of truth
        |
        +--> ~/.agents/skills/       user-scoped links
        |
        +--> <repo>/.agents/skills/  project-scoped links
```

Keep edits in the source library. Never edit mounted copies as though they were independent files.
