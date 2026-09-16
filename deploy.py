#!/usr/bin/env python3
"""Backward-compatible entrypoint for skill-librarian.

New scoped commands live in skill-librarian/scripts/skill_librarian.py. Existing
`python3 deploy.py`, `--dry-run`, and `--skill NAME` behavior remains available.
"""
from pathlib import Path
import runpy
import sys

SCRIPT = Path(__file__).resolve().parent / "skill-librarian" / "scripts" / "skill_librarian.py"
sys.argv[0] = str(SCRIPT)
runpy.run_path(str(SCRIPT), run_name="__main__")
