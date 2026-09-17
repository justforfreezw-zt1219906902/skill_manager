# Vendored project skills

Vendoring copies a canonical skill into a project's runtime directory as a real, self-contained snapshot. Use it when a project must carry the skill in Git, CI, containers, remote workspaces, or another machine without depending on the local canonical library path.

Vendoring is different from mounting:

```text
mount
  canonical skill -> project runtime symlink/junction
  follows canonical changes immediately

vendor
  canonical skill -> physical project copy
  stays pinned until `vendor update`
```

## Commands

Create a vendored snapshot:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  vendor reconstruction-geometry \
  --project /path/to/repo \
  --agent codex
```

Inspect its lifecycle state:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  vendor status reconstruction-geometry \
  --project /path/to/repo \
  --agent codex
```

Update the project snapshot from the current canonical source:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  vendor update reconstruction-geometry \
  --project /path/to/repo \
  --agent codex
```

Remove it:

```bash
python3 skill-librarian/scripts/skill_librarian.py \
  vendor remove reconstruction-geometry \
  --project /path/to/repo \
  --agent codex
```

`./bin/skill-librarian` exposes the same commands.

## Metadata and ownership

A vendored directory contains `.skill-vendor.json`. The metadata records a content fingerprint, canonical library-relative source path when available, runtime, Git revision/dirty information when available, and vendoring timestamp. It intentionally does not require a machine-local absolute canonical path to resolve future updates; the canonical skill name remains the lookup key.

The generated metadata is ignored when computing content fingerprints. `.skill-source.json` is also ignored for fingerprint comparison so provenance-only changes do not look like skill-content changes.

## Status model

`vendor status` compares three states:

- the baseline fingerprint recorded when the snapshot was created or last updated;
- the current project copy;
- the current canonical skill.

It reports:

- `SAME` - project copy and canonical source still match the baseline;
- `SOURCE_CHANGED` - canonical changed, project copy did not;
- `LOCAL_MODIFIED` - project copy changed, canonical did not;
- `DIVERGED` - both changed since the baseline;
- `SOURCE_MISSING` - canonical source no longer exists and the project copy is unchanged;
- `SOURCE_MISSING_LOCAL_MODIFIED` - canonical source is missing and the project copy also changed.

`vendor update` refuses to overwrite local project changes unless `--force` is supplied. `vendor remove` likewise refuses to delete locally modified vendored content unless `--force` is supplied.

Use `--dry-run` with create, update, or remove when previewing a mutation. Use `vendor status ... --json` for machine-readable status.

## Global inventory

Successful vendor creation registers the project's Git root in the machine-local project registry. `list --global` then shows the project copy as `VENDORED` rather than `UNMANAGED`:

```text
RUNTIME       SCOPE    PROJECT                  SKILL                     STATUS
codex         user     -                        using-agent-skills        MANAGED
codex         project  ~/projects/reconstruct   reconstruction-geometry   VENDORED
```

`VENDORED` in the global inventory means the real directory has valid `.skill-vendor.json` ownership metadata. For detailed drift information, run `vendor status`.

Vendored project copies are intentionally suitable for committing to the project repository. This differs from project `mount`, whose absolute local symlink/junction is normally machine-specific and should not be committed.
