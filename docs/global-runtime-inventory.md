# Global runtime inventory

`skill-librarian` keeps `user` and `project` as the only runtime scopes. `--global` is a read-only aggregate inventory view across the user scope and every explicitly registered project; it is not a third scope.

## Register projects

```bash
skill-librarian project add /absolute/path/to/repo
skill-librarian projects
skill-librarian project remove /absolute/path/to/repo
```

The registry is machine-local and is stored at:

- macOS/Linux: `$XDG_CONFIG_HOME/skill-librarian/projects.json` when `XDG_CONFIG_HOME` is set, otherwise `~/.config/skill-librarian/projects.json`
- Windows: `%APPDATA%/skill-librarian/projects.json` when `APPDATA` is available

`SKILL_LIBRARIAN_PROJECTS_FILE` can override the registry path for tests or advanced setups.

Only Git repository roots can be added. Passing a subdirectory inside a Git repository normalizes to that repository's root. The registry stores absolute paths and never scans the whole home directory for projects.

## Global inventory

```bash
skill-librarian list --global
```

This inspects the selected runtime's user scope plus the project scope for every registered project and adds a `PROJECT` column:

```text
RUNTIME       SCOPE    PROJECT                              SKILL                     STATUS           TARGET
codex         user     -                                    using-agent-skills        MANAGED          ~/.agents/skills/...
codex         project  ~/PycharmProjects/reconstruction     reconstruction-geometry   MANAGED          ~/ai_general_tools/...
```

The default runtime remains Codex for backward compatibility. Inspect every enabled runtime with:

```bash
skill-librarian list --global --agent all
```

Machine-readable output is available with:

```bash
skill-librarian list --global --json
skill-librarian projects --json
```

Stale registered projects do not make the whole inventory fail. Missing or non-Git entries are skipped and reported as notes; remove them with `skill-librarian project remove PATH` when they are no longer needed.

## Command routing

The global inventory commands are exposed through the repository entrypoint `bin/skill-librarian`. Existing commands continue to delegate to `skill-librarian/scripts/skill_librarian.py` unchanged.
