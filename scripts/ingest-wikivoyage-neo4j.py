#!/usr/bin/env python3
"""Deprecated: use scripts/ingest-wiki-neo4j.py (Wikipedia wp_pages → Neo4j)."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

print(
    "DEPRECATED: scripts/ingest-wikivoyage-neo4j.py → "
    "scripts/ingest-wiki-neo4j.py (Wikipedia). Forwarding…",
    file=sys.stderr,
)
# Map legacy --page to --city
argv = []
i = 1
while i < len(sys.argv):
    arg = sys.argv[i]
    if arg == "--page" and i + 1 < len(sys.argv):
        argv.extend(["--city", sys.argv[i + 1]])
        i += 2
        continue
    argv.append(arg)
    i += 1
sys.argv = [str(Path(__file__).resolve().parent / "ingest-wiki-neo4j.py"), *argv]
runpy.run_path(sys.argv[0], run_name="__main__")
