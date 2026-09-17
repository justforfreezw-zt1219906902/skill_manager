# Vendor CLI examples

```bash
# Create a physical project-owned snapshot
python3 skill-librarian/scripts/skill_librarian.py \
  vendor reconstruction-geometry \
  --project /path/to/repo \
  --agent codex

# Inspect baseline/source/local drift
python3 skill-librarian/scripts/skill_librarian.py \
  vendor status reconstruction-geometry \
  --project /path/to/repo \
  --agent codex

# Update from canonical source
python3 skill-librarian/scripts/skill_librarian.py \
  vendor update reconstruction-geometry \
  --project /path/to/repo \
  --agent codex

# Remove the vendored copy
python3 skill-librarian/scripts/skill_librarian.py \
  vendor remove reconstruction-geometry \
  --project /path/to/repo \
  --agent codex

# Aggregate user + registered project runtimes
python3 skill-librarian/scripts/skill_librarian.py list --global
```
