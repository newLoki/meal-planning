#!/usr/bin/env python3
"""
Validate plan files against schema/plan.schema.json before you commit them.

Usage:
    python validate.py                 # validate every plans/*.json
    python validate.py plans/2026-W29.json   # validate one file

Requires the dev dependency:  pip install -r requirements-dev.txt
"""
import glob
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    sys.exit("jsonschema not installed. Run: pip install -r requirements-dev.txt")

ROOT = Path(__file__).parent
SCHEMA = json.loads((ROOT / "schema" / "plan.schema.json").read_text())


def validate(path: str) -> bool:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"  ✗ {path}: invalid JSON — {e}")
        return False
    try:
        jsonschema.validate(data, SCHEMA)
    except jsonschema.ValidationError as e:
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        print(f"  ✗ {path}: {loc}: {e.message}")
        return False
    print(f"  ✓ {path}")
    return True


def main() -> None:
    args = sys.argv[1:]
    files = args or sorted(glob.glob(str(ROOT / "plans" / "**" / "*.json"), recursive=True))
    if not files:
        sys.exit("No plan files found.")
    ok = all(validate(f) for f in files)
    print("All valid." if ok else "Validation failed.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
