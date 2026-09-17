# Global runtime inventory

`skill-librarian` keeps `user` and `project` as the only runtime scopes. `--global` is a read-only aggregate inventory view across the user scope and every explicitly registered project; it is not a third scope.

## Register projects

Both supported entrypoints expose the same commands:

```bash
./bin/skill-librarian project add /absolute/path/to/repo
python3 skill-librarian/scripts/skill_librarian.py project add /absolute/path/to/repo

./bin/skill-librarian projects
python3 skill-librarian/scripts/skill_librarian.py projects

./bin/skill-librarian project remove /absolute/path/to/repo
python3 skill-librarian/scripts/skill_librarian.py project remove /absolute/path/to/repo
```

The registry is machine-local and is stored at:

- macOS/Linux: `$XDG_CONFIG_HOME/skill-librarian/projects.json` when `XDG_CONFIG_HOME` is set, otherwise `~/.config/skill-librarian/projects.json`
- Windows: `%APPDATA%/skill-librarian/projects.json` when `APPDATA` is available

`SKILL_LIBRARIAN_PROJECTS_FILE` can override the registry path for tests or advanced setups.

Only Git repository roots can be added. Passing a subdirectory inside a Git repository normalizes to that repository's root. The registry stores absolute paths and never scans the whole home directory for projects.

New successful project-scoped `mount` and `adopt` operations automatically register their Git root. Project mounts created before this feature need a one-time migration:

```bash
cd /path/to/existing/project
python3 /path/to/skill_manager/skill-librarian/scripts/skill_librarian.py project add .
```

For example, an existing project that already has `reconstruction-geometry` mounted at project scope only needs this one registration step; the mount itself does not need to be recreated.

## Global inventory

Both entrypoints are equivalent:

```bash
./bin/skill-librarian list --global
python3 skill-librarian/scripts/skill_librarian.py list --global
```

This inspects the selected runtime's user scope plus the project scope for every registered project and adds a `PROJECT` column:

```text
RUNTIME       SCOPE    PROJECT                              SKILL                     STATUS           TARGET
codex         user     -                                    using-agent-skills        MANAGED          ~/.agents/skills/...
codex         project  ~/PycharmProjects/reconstruction     reconstruction-geometry   MANAGED          ~/ai_general_tools/...
```

The default runtime remains Codex for backward compatibility. Inspect every enabled runtime with:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --global --agent all
```

Machine-readable output is available with:

```bash
python3 skill-librarian/scripts/skill_librarian.py list --global --json
python3 skill-librarian/scripts/skill_librarian.py projects --json
```

Stale registered projects do not make the whole inventory fail. Missing or non-Git entries are skipped and reported as notes; remove them with `skill-librarian project remove PATH` when they are no longer needed.

## Command routing

`skill-librarian/scripts/skill_librarian.py` is now the single routing authority. `bin/skill-librarian` is only a thin executable launcher for that Python entrypoint, so the two invocation styles expose the same command surface and behavior.
