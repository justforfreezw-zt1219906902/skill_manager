# skill-librarian — onboarding guide (for the agent)

You're in the **skill-librarian** repo: the framework for making agent skills self-contained and portable, then linking them into agent runtimes (Claude Code, Codex, and others). Each skill's folder is the source of truth; runtimes get symlinks (directory junctions on Windows), so an edit or a `git pull` propagates everywhere.

When the user asks you to **get started** or help them set this up, walk them through the onboarding below interactively, one step at a time, confirming before anything that writes or links.

## Onboarding

1. **Prerequisites.** Confirm Python 3 (`python3 --version`) for `deploy.py`, and `uv` (`uv --version`) — the default for isolating skills' Python dependencies (`brew install uv`, or https://docs.astral.sh/uv/). Migrated skills declare their deps inline and run via `uv run`, so nothing installs into the global/system Python.

2. **Pick deploy targets + libraries.** Copy the example config:
   ```bash
   cp deploy.example.json deploy.json
   ```
   Then edit `deploy.json` with the user (absolute paths, no `~`):
   - `targets` — the runtime skill dirs to deploy into. Defaults are `~/.claude/skills` and `~/.codex/skills`; keep whichever runtimes they actually use.
   - `libraries` — repos of *their own* skills to deploy alongside skill-librarian. If they don't have one yet, leave it `[]`; they can add it after step 5.

   `deploy.json` is git-ignored, since it's machine-specific.

3. **Preview, then deploy.**
   ```bash
   python3 deploy.py --dry-run   # show what it will link, change nothing
   python3 deploy.py             # create the links
   ```
   This links `skill-librarian` (and any configured libraries) into the targets. If a target already holds a real (drifted) copy of a skill, deploy prompts before replacing it. It never deletes a real directory without a yes.

4. **Confirm it loaded.** Have the user reload their agent and check that `skill-librarian` shows up in its available skills.

5. **Show them the point.** Now they can make their *own* skills portable. Point skill-librarian at a skill folder: *"skill-librarian: bring `~/path/to/some-skill` into the library."* It migrates the skill (vendors its scripts, lifts secrets and absolute paths into a git-ignored `config.json`, moves state to `~/.local/state/skills/<name>/`, then verifies by actually running it) and offers to deploy it.
   - If they don't have a private skill library yet, offer to create one: a git repo that holds skill folders. Add its path to `deploy.json`'s `libraries`, then run `python3 deploy.py`.

## Where the detail lives

- `README.md` — the conventions: what "self-contained" means, the config/state rules, and how `deploy.py` works.
- `skill-librarian/SKILL.md` — how a migration runs (its seven phases — including a human sign-off gate after the audit and a full end-to-end run by a fresh subagent), with `references/` holding a worked example and copy-paste recipes.

## Ground rules

- Show the user before you link, replace, or delete anything. `deploy.py --dry-run` previews, and deploy prompts before touching a real directory.
- This is the public framework repo and carries no personal data. The user's own skills belong in a separate, private library that `deploy.json` points at.
