#!/usr/bin/env python3
"""Patch openai SDK to tolerate None `response.output` in Codex Responses API.

The Codex /responses endpoint can return `output: null` in some streaming
events (e.g., before tool calls arrive). The vendored openai SDK iterates
`response.output` unconditionally, raising:

    TypeError: 'NoneType' object is not iterable

at openai/lib/_parsing/_responses.py:61 inside `parse_response()`.

This script idempotently patches the installed openai SDK to coerce None
to an empty list. Re-run after every `pip install -U openai` or `hermes update`.

Usage:
    python scripts/patch_openai_sdk.py
"""
from __future__ import annotations
import sys
from pathlib import Path

try:
    import openai
except ImportError:
    print("ERROR: openai SDK not installed", file=sys.stderr)
    sys.exit(1)

OLD = "    for output in response.output:"
NEW = "    for output in (response.output or []):"

sdk_path = Path(openai.__file__).parent / "lib" / "_parsing" / "_responses.py"
if not sdk_path.exists():
    print(f"ERROR: not found: {sdk_path}", file=sys.stderr)
    sys.exit(1)

src = sdk_path.read_text(encoding="utf-8")

if NEW in src:
    print(f"✓ Already patched: {sdk_path}")
    sys.exit(0)

if OLD not in src:
    print(f"⚠ Pattern not found; openai SDK layout may have changed: {sdk_path}",
          file=sys.stderr)
    sys.exit(2)

sdk_path.write_text(src.replace(OLD, NEW), encoding="utf-8")
print(f"✓ Patched {sdk_path}")
print("  (clear venv __pycache__ + restart hermes gateway for changes to load)")
