---
name: skill-librarian
description: Use when the user wants to make an agent skill self-contained and portable, or file a skill into a central skill library so it works on any machine and across agent runtimes (Claude Code, Codex, Hermes, OpenClaw, …). Migrates a skill out of wherever its runtime keeps skills (e.g. ~/.claude/skills or a project's .claude/skills) into the skill-library repo, vendoring in the scripts it depends on, extracting secrets and hardcoded paths into config.json, relocating state files, and then VERIFYING the result by actually running it. Triggers on "skill-librarian", "add to skill library", "make this skill portable", "self-contain this skill", "vendor this skill", "package this skill", "migrate this skill". Reach for this whenever a skill needs to move and keep working — a plain copy silently breaks scripts, leaks secrets into git, or drags in a .venv.
---

# Skill Librarian

Migrate an agent skill into a central **skill-library** as a fully self-contained, portable unit — then prove it still works.

A skill is a folder of instructions (plus maybe some scripts) that an agent runtime loads. This came up around Claude Code skills in `~/.claude/skills/`, which is the running example throughout — but the problem and the fixes are runtime-agnostic. Migrating skills out of Codex, Hermes, OpenClaw, or anything else works the same way: point the librarian at the skill's folder, and treat that runtime's entry/manifest file the way these instructions treat `SKILL.md`.

## Why this exists

A skill that lives in `~/.claude/skills/<name>/SKILL.md` often isn't really self-contained. It calls a script three directories away, reads an API token from a repo's `.env`, hardcodes `/Users/you/...` paths, or scribbles a running log into the repo it happened to live in. Copy that folder to another machine — or hand it to a friend, or load it into a different agent — and it breaks in ways that only show up at runtime.

This skill fixes that, one skill at a time. The output is a folder that someone else can clone, fill in one `config.json`, and run.

The bar to clear: **after migration, the skill works from its new home with nothing outside its own folder except (a) other skills it explicitly depends on and (b) the values in `config.json`.**

## Configuration

The one input per run is **the path to the source skill folder** — the user points you at a folder; you don't hunt for it. `config.json` holds only the machine-level settings reused across every migration.

Read `config.json` from this skill's folder (`$SKILL_DIR/config.json`, where `$SKILL_DIR` is the directory containing this `SKILL.md`). If it's missing, copy `config.example.json` to `config.json` and ask the user to confirm the paths. See `README.md`.

| Key | Meaning |
|-----|---------|
| `skill_library_path` | Destination repo where migrated skills land, as an **absolute** path (e.g. `/Users/<username>/source/skill-library`) |
| `state_root` | Where relocated state goes, absolute (e.g. `/Users/<username>/.local/state/skills`) |

### Path style — one rule, no exceptions

Every path in any `config.json` is a full, literal, **absolute** path, used verbatim with no expansion (never `~`, never `$HOME`). `config.json` gets read by Python, bash, and argparse, and they expand `~`/`$HOME` differently — argparse not at all — so a stored tilde is a latent bug. `config.example.json` shows paths as `/Users/<username>/...`. Tilde / `$HOME` / XDG expansion is fine **only inside code** (a tool's own state dir, a default output location), which is a single known context.

### Notation

Two token styles appear below and mean different things: **`$NAME`** is a live shell variable the commands set and reuse (`$SKILL_DIR`, `$FRAMEWORK`, `$HOME`); **`<name>`** is a fill-in token — replace it with the real value (the skill's name, a `config.json` value, a sample arg) before running.

## The workflow

Work one skill at a time. Create a TodoWrite list with the six phases below so nothing gets skipped — the verify and clean-up phases are the ones that are easy to drop and the most expensive to skip.

```
Phase 0  Locate & guard      → find the skill; bail if it already exists in the library
Phase 1  Audit dependencies  → classify everything the skill touches
Phase 2  Migrate             → copy the body, vendor scripts, extract config, relocate state
Phase 3  Verify              → grep-clean, RUN it, confirm secrets are git-ignored
Phase 4  Report              → tell the user what moved, what config they must fill in
Phase 5  Deploy & clean up   → link the new skill into the runtimes, then tidy up with the user
```

### Phase 0 — Locate & guard

1. Take the source skill folder the user points you at. Read its entry file (`SKILL.md`, or the runtime's equivalent) and settle on the skill's name — default to the folder name unless the manifest says otherwise.
2. Check `<skill_library_path>/<name>/`. **If it already exists, stop and report back** — do not overwrite. Migrations are not idempotent (config.json may hold real secrets; the body may have local edits). Let the user decide how to reconcile.
3. Confirm the destination name with the user if it's ambiguous.

### Phase 1 — Audit dependencies

Read the `SKILL.md` and list every file in the source folder. Then classify everything it touches. This audit drives the whole migration, so be thorough — grep the body and any scripts for path-like and secret-like strings.

| What you find | How to detect it | Action in Phase 2 |
|---------------|------------------|-------------------|
| **External script / project** | references to `scripts/...`, `../`, another repo path; a script the body calls | **Vendor** it into `<skill>/scripts/` (respect .gitignore — see below) |
| **Shared rules/docs from a workspace** (not a skill) | markdown/CSS/prompts the body *reads* that live in another repo or workspace | **Vendor** into `<skill>/references/<source>/` (provenance-named subfolder) |
| **Dependency on another skill** | "use the X skill", `Skill(...)`, `/some-skill` | **Reference only** — never copy. Note it as a prerequisite in the skill's README. |
| **User-provided secret** | an API key/token/password the user supplies and pastes in | **Extract** to `config.json` |
| **Tool-obtained credential** | a token the tool fetches/refreshes itself (login, OAuth, device flow) and writes to disk | **Relocate** to `<state_root>/<name>/` and point the tool there — it's state the tool manages, not user config |
| **Hardcoded absolute path** | `/Users/...`, a vault path, a fixed data dir | **Extract** to `config.json` (absolute) |
| **Output / export destination** | where the skill writes the user's data or reports | Expose as a `config.json` key with a portable default; leave the *content* where the user expects it (don't move it into the state dir) |
| **State the skill writes about itself** | log, ledger, "running list", "last processed", a marker/`.json` it appends to | **Relocate** to `<state_root>/<name>/` |

Two judgment calls come up a lot:
- **State vs. output:** "if this file vanished, would the skill lose memory of what it's done?" Yes → state → relocate. Just a deliverable the user reads → output → leave the content, but make its destination a config key.
- **Secret vs. credential:** did the *user* hand you this secret (→ `config.json`), or did the *tool* obtain it through a login/OAuth flow and write it to disk (→ `state_root/<name>/`)? The Snipd-style `login` token is the latter.

### Phase 2 — Migrate

Build `<skill_library_path>/<name>/`.

**a. Copy the body.** Copy `SKILL.md` verbatim first; you'll edit the copy, never the original. The original keeps running until the user decides to switch over.

**b. Vendor scripts — but respect the source's `.gitignore`.** Before copying a script or project folder in, read the source repo's `.gitignore` (it's often at the repo root, not next to the script). Never carry across:
- `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `*.egg-info/`
- `node_modules/`, `dist/`, `build/`, `.next/`, `.turbo/`
- `.env` and anything else the repo ignores

For a Python tool, vendor the source files only — never the virtualenv, and **never depend on a globally `pip install`ed package** (that pollutes the machine's Python and collides across skills). Isolate dependencies with **`uv`** (the library default; a documented prerequisite):
- **Single-file script** → declare its deps inline with PEP 723 and run it via `uv run` (uv builds a cached, isolated env per dependency-set; nothing touches global Python):
  ```python
  #!/usr/bin/env python3
  # /// script
  # requires-python = ">=3.10"
  # dependencies = ["pypdf"]
  # ///
  ```
- **Multi-file project** → make a venv in the state dir and install into it: `uv venv <state_root>/<name>/.venv && uv pip install ...`, then invoke scripts with that interpreter. The venv is machine-specific, so it lives with state, not in the committed folder.

For a Node tool, copy `package.json` (and lockfile), not `node_modules`. The goal is "reproducible **and isolated** on a fresh machine," not "byte-for-byte copy."

**Where vendored things go — two standard folders, don't invent more:** runnable code → `<skill>/scripts/`. Reference material the body reads (rules, prompts, CSS, docs) → `<skill>/references/`; when it's borrowed from another skill or workspace, use a provenance-named subfolder `<skill>/references/<source>/` (e.g. `references/content-to-guide/`).

**c. Rewrite the vendored scripts to be location-independent.** A vendored script must resolve its own location and read config from the skill root, not from wherever it used to live:
```python
# was: load_dotenv(Path('/Users/me/some-repo/.env'))
config = json.loads((Path(__file__).resolve().parent.parent / "config.json").read_text())
token = config.get("some_token") or os.getenv("SOME_TOKEN")  # env fallback is a nice touch
```
Drop now-unneeded deps (e.g. `python-dotenv`) from the script's inline `dependencies` list.

**d. Extract user-provided secrets & paths into config.** For every user secret and hardcoded path from the audit:
- Add a key to `config.example.json` with a placeholder that shows the shape: paths as absolute `/Users/<username>/...`, secrets as `your_<thing>_here`.
- Add the real value to `config.json` (git-ignored), as an **absolute** path (expand any `~`/`$HOME` before writing). When the value already exists on this machine (a token in a repo `.env`, a known path), copy it in programmatically, **without printing the secret to the terminal**.
- In `SKILL.md`, replace the literal with a `config.json` read, used verbatim (no expansion). Establish the `$SKILL_DIR` convention near the top so every command can find config:
  ```bash
  vault_path=$(python3 -c "import json; print(json.load(open('$SKILL_DIR/config.json'))['vault_path'])")
  ```
  Tool-obtained credentials are *not* config — they go to state, see (e).

**e. Relocate state and tool-obtained credentials.** Point every state write — and any credential the tool fetches for itself (a `login`/OAuth token it writes to disk) — at `<state_root>/<name>/`. For a vendored tool that hardcodes something like `~/.foo_token`, repoint its token path to the standard state location and create the dir on write:
```python
_STATE = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), "skills", "<name>")
os.makedirs(_STATE, exist_ok=True)
TOKEN_FILE = os.path.join(_STATE, "token")
```
Migrate existing token/state by **copy** if the original location is still used elsewhere (e.g. a scheduled job that points at it), otherwise move it.

**f. Generate the support files** (the portable-skill pattern):
- `config.example.json` — committed, shows every key with placeholders (absolute `/Users/<username>/...` paths, `your_*_here` secrets), no real values
- `config.json` — git-ignored, real values for this machine
- `.gitignore` — contains `config.json` (a repo-root `.gitignore` with `**/config.json` also works; add a local one to be safe)
- `README.md` — human setup: copy config, fill keys, note `uv` as a prerequisite (deps are isolated via `uv run` / a state-dir venv, never installed globally), list any prerequisite skills and MCP servers

A skill with no secrets, no hardcoded paths, no scripts, and no state is just `SKILL.md` — a one-file copy. Don't manufacture config files it doesn't need.

### Phase 3 — Verify (do not skip)

A migration that *looks* moved but silently broke a path is worse than no migration — it fails when the user is depending on it. Run the same three checks every time, after the copy/edits are done:

1. **Grep clean.** No leftover source coupling in the migrated `SKILL.md` and scripts:
   ```bash
   grep -rnE "/Users/[a-z]+|\.env|load_dotenv|<source-repo-name>|os\.getenv\(['\"][A-Z_]+" <skill_library_path>/<name>
   ```
   Then **classify each hit** rather than reacting to the count. A literal `/Users/<you>/…` the code actually *uses* is real coupling: go back to Phase 2. But a skill that is *about* paths, or written in someone's voice, legitimately contains example paths and names in its prose — those are illustrative, not coupling. What must be gone is functional coupling: real machine paths the code reads, `.env`/`load_dotenv`, the source repo name in a code path. Fine to leave: `$SKILL_DIR`, `config.json` reads, `<state_root>`, and in-code `~/`/`$HOME` defaults.

2. **Run it — through the isolated env, not global Python.** Execute every vendored script from the new location so it exercises both the dependency isolation and the `config.json` read:
   ```bash
   SKILL_DIR=<skill_library_path>/<name>; uv run "$SKILL_DIR/scripts/<script>.py" <sample-args>
   ```
   Running with `uv run` proves the deps resolve from the script's own declaration (or the skill's state-dir venv) — so "it works" never means "it works because a package happens to be installed globally." (Quick sanity check: `uv run python -c "import <dep>"` *without* the script should fail — confirming nothing leaks from the ambient environment.) If a script can't fully run here (needs interactive auth, a device, live data), at minimum import-check it through `uv run` and confirm it finds and parses `config.json`. Note in the report what was fully run vs. smoke-tested. Read the raw output — a warning on stderr is fine, a traceback or empty stdout is not.

3. **Secret-safety.** Confirm git ignores the secret and tracks the example:
   ```bash
   git -C <skill_library_path> check-ignore <name>/config.json   # must print the path
   git -C <skill_library_path> status --short <name>/            # config.example.json staged, config.json absent
   ```

If any check fails, fix it before moving on. Don't report success on an unverified migration.

### Phase 4 — Report

Tell the user, concisely:
- **Moved/vendored:** which scripts came across, which were dropped (`.venv`, etc.)
- **Config keys created:** and which you auto-filled vs. which they must fill in
- **State relocated:** old path → `<state_root>/<name>/`
- **Prerequisite skills/MCPs:** anything referenced but not copied
- **Verification:** what ran clean, what was only smoke-tested
- **Not committed** unless they asked — leave staging to them

### Phase 5 — Deploy, then clean up

The migration is verified and reported. Now **deploy the skill** so it actually loads, then tidy up. Deploying is something you do (safely — see below); the cleanup items are offered, and nothing that touches files the user already had goes ahead without a yes.

- **Deploy it — do this yourself, don't just hand over a command.** Deploying links the skill into every runtime's skills dir (symlink on macOS/Linux, directory junction on Windows) so it loads now, and future edits / `git pull`s propagate everywhere. `deploy.py` lives at the root of the skill-librarian framework repo, one level up from this skill's own folder. Run it:
  1. **Locate `deploy.py`** by resolving this skill's *real* path (it's usually symlinked into the runtime dir, so follow the link):
     ```bash
     FRAMEWORK=$(python3 -c "import os,sys; print(os.path.dirname(os.path.dirname(os.path.realpath(sys.argv[1]))))" "$SKILL_DIR/SKILL.md")
     ```
  2. **Register the skill's library.** `deploy.py` only sees skills in the libraries listed under `libraries` in `$FRAMEWORK/deploy.json`. The skill you just migrated lives in `skill_library_path`; if that path isn't already in `deploy.json`'s `libraries`, add it (or ask the user to), or `--skill` won't find it.
  3. **Preview, then link:**
     ```bash
     python3 "$FRAMEWORK/deploy.py" --dry-run --skill <name>   # show the plan
     python3 "$FRAMEWORK/deploy.py" --skill <name>             # do it
     ```
     `deploy.py` prompts before replacing any real (drifted) directory, so running it is safe; nothing destructive happens without a yes.
- **The original skill.** Once deployed, the source copy is redundant and will **double-load** if it sits in a dir the same runtime also reads. Lay out the options:
  - **Delete it** (recommended once deploy succeeds) — the verified library copy now loads via the link, so the original is a stale duplicate. Confirm before deleting.
  - **Leave it** — only if it's in a dir the runtime doesn't also load; otherwise it collides with the deployed link.
- **Migration scratch.** The verify run may have left `__pycache__/` or `*.pyc` in the vendored scripts, or temp/test output. Offer to remove it. (`git -C <skill_library_path> status --short <name>/` shows anything stray.)
- **Now-orphaned source scripts** you vendored in (e.g. an external `scripts/foo.py`). They may be removable from the source repo, but only if nothing else uses them — the user knows that, you don't. Point them out.
- **Old state files** at the pre-migration location, if you relocated state and didn't move the history across.

## Conventions this skill enforces

- **`$SKILL_DIR`** = the folder holding `SKILL.md`. The running agent resolves it; config is always `$SKILL_DIR/config.json`.
- **Two standard folders:** `scripts/` for runnable code, `references/` for docs the skill reads on demand (borrowed material in `references/<source>/`). Don't invent new top-level folders.
- **Dependencies are isolated with `uv`** — PEP 723 inline deps + `uv run` for single-file scripts, a state-dir venv for projects. Never `pip install` into the global/system Python.
- **Scripts self-locate** via `Path(__file__)` and read config from the skill root — they never assume a working directory.
- **State** lives under `<state_root>/<name>/`, never inside the skill folder (keeps the folder a clean, shareable artifact) and never inside a random repo.
- **Secrets** never appear in committed files or terminal output.

## Golden reference

`references/case-study-daily-summary.md` walks through one full migration end to end — a skill with a vendored script, an extracted secret, and an extracted path — showing how the phases play out and what the finished folder looks like. Read it when you want a worked example to match. `references/migration-recipes.md` has the atomic before/after snippets for the common cases (dotenv→config, hardcoded path→config, state relocation, vendoring a Python CLI).

## Dogfooding

This skill follows its own pattern: its machine-specific paths live in `config.json`, and it carries a `config.example.json`, `.gitignore`, and `README.md`. It belongs in the library too — migrating it is a good self-test.
