# Determinism audit — compiling the mechanical half of a skill

Companion to Phase 1b in `SKILL.md`. This is the reasoning, the pattern, and
two worked examples (2026-07-19/20) that turned multi-hour nightly failures
into fast, testable runners.

## The problem this solves

A skill that runs headlessly (a launchd/cron job invoking `claude -p`) executes
its whole workflow through an agent session. Auditing real jobs showed the
typical shape: **~10 of 11 tool calls are plumbing** — `date`, file reads,
selection-by-rule, an email send, a log append — and exactly one step is real
LLM work. That plumbing is not just wasted model effort; every tool call is a
place a headless run can die in ways an interactive run never shows:

- **Permission-gate hang** — a tool call needs an approval nobody can give.
  Proven from a session transcript: first two tool calls issued at t+1s, then
  `cpu=0.0%` for exactly 3600s until the wrapper killed it (`aidb-writeup`,
  3 of 6 runs).
- **OS-level (TCC) hang** — macOS denies the claude binary a protected path
  (iCloud Drive, ~/Documents); the agent then *improvises* workarounds until
  one raises an invisible consent dialog and blocks forever
  (`regenerate-context-snapshots`, six consecutive 2h burns).

The second case carries the sharper lesson: **in unattended jobs, agent
adaptivity is a liability.** A deterministic runner fails fast and loud where
an agent flails expensively — and a wrong-but-plausible improvisation is worse
than an error.

## The compile pattern

Split the workflow along the Phase 1b classification:

```
runner script (scripts/run.py)          one toolless call per judgment step
─ mechanical steps, in code ──────────▶ claude -p "<brief>" \
   date/selection/reads                    --allowedTools "" \
   (fail fast if inputs missing)           --strict-mcp-config \
                                           --mcp-config '{"mcpServers":{}}'
                                        ▲ input on STDIN, output on STDOUT
─ validate output (guards) ◀────────────┘
─ deliver / log / summarize, in code
```

Rules that make it hold up:

1. **Zero tools on the LLM call.** `--allowedTools ""` + empty MCP config.
   No tools → no permission gates, no filesystem, no TCC exposure. The hang
   modes above become structurally impossible, not just unlikely.
2. **Voice stays in the skill, never in code.** The runner extracts the
   judgment steps' prose from `SKILL.md` (and any sibling-skill briefs) at
   runtime and passes it verbatim. Editing `SKILL.md` still changes both the
   interactive and scheduled behavior; nothing drifts.
3. **Deterministic guards on the output, atomic writes.** Check required
   structure (sections, stamps, minimum size, no prompt/corpus leakage) before
   the result replaces anything live; keep a `.rejected` copy on failure and
   exit non-zero so the wrapper alerts. Salvage only *known-benign* deviations
   deterministically (e.g. trim a one-line preamble when the required first
   line appears within the first few lines) — never "fix" content.
4. **Fail fast on missing inputs.** If a source dir is unreadable, raise
   immediately. The runner must never re-create the improvisation problem.
5. **Mechanical logic gets unit tests.** Selection, curation caps, and the
   guards are pure functions — test them (the two examples ship 11 and 8
   tests). The generative step is verified by `--dry-run` + a human eyeball.
6. **Ship a `--dry-run`** that does everything except the outward side-effect
   (email/post/overwrite), and a targeted rerun flag (`--only <output>`) so a
   single rejected output doesn't force redoing the rest.
7. **Billing follows the CLI.** Calling the `claude` binary keeps the work on
   the user's subscription; compiling to a raw API call silently converts it
   to metered spend. Don't change that without the user's say-so.
8. **Module naming:** never name a vendored module after a stdlib module
   (`select.py` shadowed stdlib `select` and would have crashed `subprocess`
   at its first `communicate()`). Prefix it (`episode_select.py`).

## Worked example 1 — `aidb-writeup` (skill, migrated + compiled)

Nightly newsletter job: pick the newest unprocessed podcast episode, write a
newsletter + LinkedIn drafts in the user's voice, email it, log it. As an agent
session: 11 gated tool calls, of which one (`Write` the newsletter) was
judgment. Compiled during its library migration:

- `scripts/episode_select.py` — the selection rule (was a bash block inside
  SKILL.md), pure + 9 tests
- `scripts/run.py` — date/read/email/log in Python; ONE toolless call writes
  the newsletter, transcript on stdin; guards check sections + ≥5 timestamps
- `SKILL.md` remains the voice contract; run.py extracts Steps 4–6 verbatim
- Result: hang impossible; a "nothing to do" run costs zero LLM work
  (previously it spun up a full Opus session to discover an empty backlog)

## Worked example 2 — `regenerate-context-snapshots` (not a skill — same pattern)

Nightly rebuild of two flat context files from an Obsidian vault in iCloud.
The agent form died of the TCC hang. Compiled outside the library (it has no
interactive trigger, so it stayed in its automation repo — the audit applies
to any headless claude job, skill or not):

- Python reads the vault — plain bash/python demonstrably held the launchd TCC
  grant (a sibling git-autocommit job reads the same vault hourly) even while
  the claude binary was denied
- Curation made literal (per-file byte caps, "skim" = first 30 lines) instead
  of prose asking the model to "skim"
- Two toolless calls (one per output file), briefs in a sibling `.prompt.md`,
  guards + atomic replace + `--only` rerun
- First live run **proved the guards**: one call opened with "I'll write the
  dossier now." before the required stamp line — rejected, live file untouched,
  `normalize()` added with a regression test

## Reporting the verdict (Gate)

State it in one or two lines, with the ratio and the recommendation:

> Determinism: 9 of 11 steps mechanical (selection, reads, email, log);
> judgment = the newsletter + drafts. Runs unattended nightly → recommend
> compiling: `scripts/run.py` + one toolless call; SKILL.md stays the voice
> contract.

or, for the leave-alone case:

> Determinism: workflow is exploratory debugging — nearly all judgment, and it
> only runs interactively. Leave as prose; nothing to compile.
