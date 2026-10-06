# Upstream lifecycle

Upstream state and runtime ownership are independent:

```text
upstream Git repository -> canonical skill -> runtime link or project vendor
       upstream status/update                 list --global / vendor status
```

`MANAGED` means a runtime link points at an active canonical skill. It does **not** mean the skill is current upstream. `VENDORED` means a project owns a pinned copy, not a live upstream subscription.

These commands work through both entrypoints:

```bash
python3 skill-librarian/scripts/skill_librarian.py upstream status --all
./bin/skill-librarian upstream status --all
```

## 1. Inspect every active canonical skill

```bash
./bin/skill-librarian upstream status --all
./bin/skill-librarian upstream status --all --json
./bin/skill-librarian upstream status shipping-and-launch --json
```

This follows configured canonical libraries, not just mounted runtime entries. Recursive discovery, skill boundaries, ignored categories and retired skills keep their existing semantics. Duplicate active names produce errors rather than silently updating the first match.

The comparison covers the whole skill tree: file contents, relative paths, executable bits, relative symlink targets, scripts, assets, references and empty directories. Generated root `.skill-source.json` and nested VCS metadata are excluded from fingerprints. A matching `SKILL.md` alone is not enough.

| Status | Meaning | Update behavior |
| --- | --- | --- |
| `SAME` | Local payload matches the selected upstream snapshot | No content change; stale provenance can be refreshed |
| `OUTDATED` | Local payload matches its recorded baseline; upstream differs | Eligible after validation, unless the upstream skill disappeared |
| `LOCAL_MODIFIED` | Local differs from baseline; upstream has not changed | Blocked |
| `DIVERGED` | Both differ from baseline, and local does not match upstream | Blocked |
| `UNTRACKABLE` | No usable Git source and exact baseline revision | Blocked; migrate provenance or manage manually |
| `ERROR` | Invalid metadata, inaccessible source, unsafe tree, duplicate name, etc. | Blocked; the rest of a batch is still inspected |

Failures are cached and reported per skill; one bad source does not suppress other results. Checkouts are reused within the command for the same source URL and ref. There is no stale on-disk result cache. Git operations have a 120-second per-process timeout and disable terminal credential prompts. Use your existing Git credential manager or SSH configuration, not credentials in a source URL.

The JSON envelope is `{ "schema_version": 1, "skills": [...], "summary": {...} }`. Status rows include baseline/upstream revisions, fingerprints, local/upstream change flags and reasons when applicable. A removed or renamed upstream path has `upstream_present: false`; it is never interpreted as permission to delete the canonical skill.

## 2. Migrate provenance for older installs

Skills copied or adopted before provenance tracking may have no `.skill-source.json`. Do not invent their installation commit or assume that today's upstream is their baseline.

Provide the intended repository explicitly and preview:

```bash
./bin/skill-librarian provenance migrate shipping-and-launch \
  --source addyosmani/agent-skills --ref main --dry-run
```

A full-tree match permits writing metadata only:

```bash
./bin/skill-librarian provenance migrate shipping-and-launch \
  --source addyosmani/agent-skills --ref main
```

If the installed copy is older, either name a known baseline revision or request a bounded history search:

```bash
./bin/skill-librarian provenance migrate shipping-and-launch \
  --source addyosmani/agent-skills --ref main \
  --baseline-ref <known-full-commit-sha> --dry-run

./bin/skill-librarian provenance migrate shipping-and-launch \
  --source addyosmani/agent-skills --ref main \
  --history-limit 100 --dry-run
```

`--history-limit` defaults to 0 and accepts 0 through 500. It searches that many commits touching the selected source path, using a separate offline Git checkout; it never rewinds your source library or changes the cached current snapshot. This is a bounded search, not a proof that no matching version ever existed. It does not follow old path renames automatically. Use `--source-path` and `--baseline-ref` when the old location is known.

Migration actions:

- `WOULD_MIGRATE` / `MIGRATED`: entire payload matches the selected or discovered revision.
- `NO_MATCH`: payload differs; **nothing is written**, and the skill stays untrackable.
- `ALREADY_TRACKED`: existing valid metadata is preserved, not overwritten.
- `ERROR`: malformed metadata, unsafe source/path, missing source skill, or other failure.

A matched commit is a verified compatible baseline, not proof of the original installation date or original publisher. Migration records `acquired_via: migration`, `migrated_at`, `baseline_verified` and a fingerprint. For compatibility with schema v1 it also records `imported_at` at migration time; this is not a reconstructed historical install timestamp. Canonical content is never replaced by migration.

### Explicit batch manifest

Copy and edit `examples/provenance-migration.example.json`; include only skills and sources you intend to associate. Category changes are irrelevant because canonical selection uses the skill name. Source paths describe the upstream repository, not the local category.

```json
{
  "schema_version": 1,
  "skills": [
    {
      "skill": "shipping-and-launch",
      "source": "addyosmani/agent-skills",
      "source_path": "skills/shipping-and-launch",
      "ref": "main",
      "history_limit": 100
    },
    {
      "skill": "karpathy-guidelines",
      "source": "multica-ai/andrej-karpathy-skills",
      "source_path": "skills/karpathy-guidelines",
      "ref": "main"
    }
  ]
}
```

```bash
./bin/skill-librarian provenance migrate --manifest migration.json --dry-run --json
./bin/skill-librarian provenance migrate --manifest migration.json --json
```

Batch migration is per-skill, not one all-or-nothing filesystem transaction: verified entries may succeed while mismatches remain untouched. No repository is selected just because its skill names look similar. Plain local sources stay untrackable by this remote-upstream workflow. Existing provenance from `import` remains compatible with the original `skill_upstream.py status` command.

## 3. Preview and apply safe updates

```bash
./bin/skill-librarian upstream update shipping-and-launch --dry-run --json
./bin/skill-librarian upstream update shipping-and-launch

./bin/skill-librarian upstream update --all --dry-run --json
./bin/skill-librarian upstream update --all --json
```

`--all` means update eligible canonical skills and report blocked ones. It does not force replacements. There is deliberately **no `--force`** for upstream update. For local modifications, divergence, an unknown baseline or a moved upstream path, resolve the situation manually rather than deleting data to make an update pass.

To bind application to the exact snapshot you reviewed:

```bash
./bin/skill-librarian upstream status shipping-and-launch --json
# Review the returned upstream_revision and upstream changes first.
./bin/skill-librarian upstream update shipping-and-launch \
  --expected-revision <reviewed-full-commit-sha>
```

The operation acquires immutable snapshots, validates the whole incoming tree, stages a copy on the destination filesystem, checks that local content and provenance have not changed, moves the original to a backup, publishes the staged snapshot, and verifies it. Caught publication/verification failures (including keyboard interrupts) restore the original. A failed rollback reports recovery paths and preserves both snapshots. Backups are retained after success.

Source scripts are **never executed** during inspection, migration or update. Import validation also rejects `.env`-style secrets, common dependency/cache directories, unsupported links and mismatched skill names. Nested local VCS metadata blocks replacement so it cannot be accidentally discarded. These are structural safeguards, not a security or behavioral review of the upstream instructions/code; review the changes before applying them.

### Pins versus moving branches

A recorded tag or commit remains pinned. `status --all` does not silently compare a pinned release against `main` or pick a newer tag. An explicit ref override is available for a single skill:

```bash
./bin/skill-librarian upstream status some-skill --ref v2.0.0 --json
./bin/skill-librarian upstream update some-skill --ref v2.0.0 --dry-run
./bin/skill-librarian upstream update some-skill --ref v2.0.0
```

The baseline is still checked against the old recorded revision. A successful update records the new tracking ref and exact revision. `--ref` and `--expected-revision` cannot be combined with `--all`.

### Backups, locks and concurrent readers

Backups live beside the canonical directory as `.skill-librarian-backup-<skill>-<id>`. Staging and lock paths use the same hidden `.skill-librarian-` prefix and are ignored by skill discovery. **Git does not ignore them automatically.** Before committing library changes, add this pattern to that library's `.gitignore` (the CLI does not edit it for you):

```gitignore
.skill-librarian-*
```

Do not ignore `.skill-source.json`: verified provenance should be committed with the skill. Inspect retained backups and delete them manually only after validating the new version. Nothing runs `git add`, commits or pushes for you.

Per-skill locks coordinate lifecycle writers; `owner.json` identifies the process. After a hard crash, inspect the PID and recovery directories before removing a stale lock. This is not a cross-machine lock or a guarantee against arbitrary editors changing files at the exact swap instant. Pause competing writers and active skill readers for an update: portable two-step directory replacement has a brief rename window. Power loss / SIGKILL can require manual recovery from the preserved backup.

`--dry-run` writes no canonical content, provenance, locks, backups or runtime entries. It can create temporary Git checkouts to inspect upstream.

## 4. Verify runtime ownership separately

```bash
./bin/skill-librarian upstream status --all
./bin/skill-librarian list --global --agent all
```

The source update preserves the canonical path. Existing Codex/Claude Code links remain links to that path; sessions may need to reload already-read instructions. Project vendor copies do not change. Inspect and update those separately using `vendor status` and `vendor update` only when the project should adopt the new snapshot.

No cloud publishing or account synchronization is part of this feature.

## Exit codes

- `0`: successful inspection/migration/update; `SAME`, `OUTDATED`, `LOCAL_MODIFIED`, `DIVERGED` and `UNTRACKABLE` are informational for **status**.
- `1`: an update was `BLOCKED` or migration found `NO_MATCH`; other batch entries may have succeeded.
- `2`: an operational/metadata error or invalid arguments. Per-skill errors stay in JSON results; top-level argument/manifest errors are written to stderr.
