# Case study: migrating `daily-summary`

A full walkthrough of one real migration, end to end. `daily-summary` is a good
teaching case because it hits every part of the pattern at once: an external
script, a secret, and a hardcoded path. Read this when you want to see how the
phases fit together on a concrete skill; `migration-recipes.md` has the
atomic before/after snippets.

## What it was before

`daily-summary` builds a unified daily report from Obsidian notes, Todoist tasks,
and Toggl time tracking. As it sat in a project's `.claude/skills/`, three things
tied it to one machine:

1. **An external script.** The body called `scripts/get_toggl_time.py`, which
   lived in the parent repo (`<repo>/scripts/`), not in the skill folder.
2. **A secret, loaded from a repo `.env`.** Both the script and some inline
   `python3 -c` blocks did `load_dotenv('<repo>/.env')` and read `TOGGL_API_TOKEN`.
3. **A hardcoded absolute path.** The Obsidian vault was a literal
   `/Users/<me>/Library/Mobile Documents/.../Vault` in the body.

It also wrote its report to `~/Downloads/<date> Daily Summary.md` — but that's
**output the user wants**, not skill state, so it was left alone.

## The audit (Phase 1)

| Found | Classification | Action |
|-------|----------------|--------|
| `scripts/get_toggl_time.py` (in parent repo) | external script | vendor into `<skill>/scripts/` |
| `TOGGL_API_TOKEN` via repo `.env` | secret | extract → `config.json` |
| Obsidian vault absolute path | hardcoded path | extract → `config.json` |
| Todoist MCP tools | another tool/MCP | reference as a prerequisite, don't copy |
| `~/Downloads/...Daily Summary.md` | user-facing output | leave as-is |
| (none) | self-state | — |

## The migration (Phase 2)

**Vendored the script** into `<skill>/scripts/get_toggl_time.py` and declared its
one dependency (`requests`) inline with a PEP 723 header, so `uv run` resolves it
in isolation — nothing installs into global Python, and there's no
`requirements.txt`/`.venv` to carry:
```python
# /// script
# requires-python = ">=3.9"
# dependencies = ["requests"]
# ///
```
The original used `python-dotenv` to read the repo `.env`; that dependency was
dropped.

**Rewrote the script to self-locate and read config:**
```python
# was: load_dotenv(Path('<repo>/.env')); os.getenv('TOGGL_API_TOKEN')
config_path = Path(__file__).resolve().parent.parent / "config.json"
token = json.loads(config_path.read_text()).get("toggl_api_token") or os.getenv("TOGGL_API_TOKEN")
```

**Extracted secret + path into config.** `config.example.json` (committed) shows
the shape; `config.json` (git-ignored) holds the real values:
```json
{ "toggl_api_token": "your_toggl_api_token_here",
  "obsidian_vault_path": "/path/to/your/vault" }
```
The real token already existed in the repo `.env`, so it was copied into
`config.json` programmatically — never echoed to the terminal.

**Rewrote the body** to read from config via the `$SKILL_DIR` convention:
```bash
vault_path=$(python3 -c "import json; print(json.load(open('$SKILL_DIR/config.json'))['obsidian_vault_path'])")
```
The inline `python3 -c` blocks that fetched Toggl project names were repointed
the same way. Every `/Users/...`, `.env`, and `load_dotenv` reference was gone.

**Generated the support files:** `config.example.json`, `config.json`,
`.gitignore` (containing `config.json`), and a `README.md` with the one-time
setup (copy config, fill keys, install `uv`, and a note that the Todoist MCP must
be connected).

## The verification (Phase 3)

1. **Grep clean** — searched the migrated body + script for `/Users/`, `.env`,
   `load_dotenv`, `os.getenv('TOGGL...`; the only path-like references left were
   `$SKILL_DIR`, `config.json`, and the portable `~/Downloads` output.
2. **Ran it** — executed `uv run get_toggl_time.py <yesterday>` from the new
   location; `uv` installed `requests` from the inline header into its isolated
   cache, the script read the token from `config.json`, hit the Toggl API, and
   returned real entries on stdout.
3. **Secret-safety** — `git check-ignore config.json` printed the path (ignored),
   and `git status` showed `config.example.json` staged but `config.json` absent.

## The result

```
daily-summary/
├── SKILL.md              # reads config.json via $SKILL_DIR; no machine paths
├── scripts/
│   └── get_toggl_time.py # self-locating; reads config.json; PEP 723 deps (requests)
├── config.example.json   # committed
├── config.json           # git-ignored (real token + vault path)
├── .gitignore            # config.json
└── README.md             # one-time setup
```

Anyone can clone this, copy `config.example.json` to `config.json`, drop in their
own Toggl token and vault path, and run it with `uv` — on any machine.
