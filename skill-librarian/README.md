# skill-librarian

Takes an agent skill and turns it into something you can actually move: it pulls in the scripts the skill depends on, lifts secrets and machine-specific paths out into a config file, gives state files a proper home, and then proves the result still works before calling it done. The output lands in a central **skill-library** repo. 

As AI users become more sophisticated skills become more than just plain SKILL.md files. Over time it starts calling a script two directories away, reading an API key from some repo's `.env`, hardcoding a path on the machine, appending to log or other state files. None of that hurts while the skill stays put. The damage shows up the moment you move it — to a new laptop, to a friend, into a different agent — and it shows up at the worst time, at runtime, when you were counting on the thing to run.

The skill library is where a skill stops being "this thing that works on my machine in this one spot" and becomes a unit you can copy, publish, and trust. The principles below are how it gets there. They're the original checklist this skill was built from, and every migrated skill comes out obeying them.

## The principles

### 1. The folder carries everything it runs

A skill is only as portable as its neediest dependency. If it leans on a script that lives elsewhere, the folder isn't really the skill; it's half the skill plus a promise about the rest of your disk. So the librarian vendors those scripts in. Small helpers come along as files. Bigger tools come as their whole project, minus the parts that don't travel (a Python `.venv`, `node_modules`, build output).

Python deps are isolated with **`uv`** (the default) — declared inline (PEP 723) for a single-file script, or rebuilt into a state-dir venv for a project — so they're reproducible anywhere and never installed into your global Python.

Once that's done, the folder *is* the skill. That's what lets you set it up on a new machine, hand it to someone else, or load it into a different runtime without touching a line.

### 2. ...except other skills, which it points at instead of copying

Skills build on each other: one drafts a report, another emails it. The exception to "carry everything" is sibling skills, which get named as prerequisites rather than copied in. If every skill embedded its own copy of `send-email`, you'd end up with a dozen versions drifting apart, and a bug fixed in one would still be live in all the rest. Referencing keeps one source of truth per capability and lets skills compose.

### 3. Secrets live in config.json, never in the skill itself

A skill you intend to share has to be safe to commit and safe to send. A token baked into the instructions or a script leaks the instant you push to GitHub or zip it up for a friend. The fix is to split "how it's wired" from "your private keys":

- `config.example.json` — the shape of the config, committed, no real values
- `config.json` — your actual secrets, git-ignored so it never leaves your machine
- `.gitignore` — ignores `config.json`
- this `README` — tells whoever receives it what to fill in

Now the skill can go public, or go to a friend who drops in their own `config.json`, and the secret stays home.

### 4. Hardcoded paths get the same treatment

This is the quieter version of the same problem. `/Users/you/vault` is correct on exactly one machine and wrong everywhere else, including your own next laptop. Lifting paths into `config.json` lets the skill say *what* it needs, a vault or a data directory, without pretending to know *where* it is. The same skill then runs for you and for someone whose folders look nothing like yours, no edits required.

### 5. If it's already in the library, stop and ask

Re-running a migration isn't safe to do on autopilot. The copy already in the library may hold a real `config.json` with live secrets, or edits you made after the first pass. Overwriting it would wipe both. So when the destination already exists, the librarian stops and hands the decision back to you instead of clobbering work in place.

### 6. State gets a central home, outside the skill

A skill produces two kinds of files, and they deserve different treatment. *Output* is what you asked for, a report or a summary, and it belongs wherever you expect your files. *State* is what the skill remembers about itself between runs: a log, a "last processed" marker, a running ledger. State has to persist, but it shouldn't clutter the shareable folder or get stranded in whatever repo the skill grew up in. So it goes to `~/.local/state/skills/<skill-name>/`, a predictable spot you can find, back up, or wipe. That way you can delete and re-clone the skill folder without erasing its memory, and share the folder without shipping your own run history.

## How it makes sure: verify first, offer cleanup last

Two of the steps are about the *process* rather than the finished skill, and they're the ones easiest to skip.

**Verify before reporting.** A migration that looks finished but quietly broke a path is worse than no migration, because nothing tells you until you're depending on it. So the librarian refuses to trust itself: it greps the result for leftover coupling, actually runs the vendored scripts from their new home, and confirms the secret is git-ignored.

**Then prove it with a fresh run.** Static checks confirm the parts; they can't confirm the whole. So the real proof is handing the finished skill to a *fresh subagent given nothing but the skill itself* and having it do a real task end to end. If a path still pointed home, a step leaned on something only this machine had, or a dependency only resolved because it happened to be installed, the fresh run is where it surfaces — and the fixes go in while they're concrete.

**Offer cleanup, never force it.** Once the new copy is verified and reported, there's tidying to do, but it's offered rather than assumed. The librarian clears the scratch a test run leaves behind, then lays out what to do with the original skill: leave it, symlink it to the library, or delete it, along with any now-orphaned scripts. For each one it tells you how or offers to do it for you, and nothing you already had gets removed without your say-so.

## Setup

1. **Config.** Copy the example and set your paths:
   ```bash
   cp config.example.json config.json
   ```
   - `skill_library_path` — the destination repo where migrated skills land (run `git init` there if it's new).
   - `state_root` — where relocated state goes (e.g. `/Users/<username>/.local/state/skills`).

   Use **absolute paths** (no `~`, no `$HOME`): config.json is read by several tools that expand the tilde differently, so a literal absolute path is the one thing they all agree on. There's no setting for where source skills live. You point the librarian at a folder per run.

2. **`uv` — required (the default).** The librarian itself ships no scripts; it drives migrations through the agent's normal file and shell tools. But it makes every migrated skill isolate its Python dependencies with [`uv`](https://docs.astral.sh/uv/) — declared inline (PEP 723) and run via `uv run`, never installed into global Python. Install it (`brew install uv`) on any machine that will *run* the skills it produces.

## Using it

Point the agent at a skill folder: *"skill-librarian: bring `~/path/to/some-skill` into the library"* (or "add this skill to the skill library"). It runs one skill at a time through seven phases — locate & guard, audit, migrate, **verify**, **prove it end-to-end** (a fresh subagent runs the whole skill on a real input), report, then **offer cleanup** — and stops to ask if the skill already exists in the library.

## Notes

- `config.json` is git-ignored, so your machine paths never get committed.
- `references/case-study-daily-summary.md` is a full worked migration to match against.
- `references/migration-recipes.md` has copy-paste snippets for the common cases (dotenv→config, hardcoded path→config, state relocation, vendoring a Python CLI).
