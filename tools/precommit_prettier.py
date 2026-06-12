"""Pre-commit hook: run prettier --check on staged frontend files.

Invoked by .pre-commit-config.yaml with one or more file paths as argv.
Exit 0 if every file passes, non-zero otherwise.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRETTIER_BIN = PROJECT_ROOT / "frontend" / "node_modules" / "prettier" / "bin" / "prettier.cjs"


def main() -> int:
    if not PRETTIER_BIN.exists():
        print(f"prettier not found at {PRETTIER_BIN}; did you `npm install` in frontend/?", file=sys.stderr)
        return 1
    node = shutil.which("node")
    if not node:
        print("node executable not found on PATH", file=sys.stderr)
        return 1

    files = [a for a in sys.argv[1:] if a]
    if not files:
        return 0

    result = subprocess.run(
        [node, str(PRETTIER_BIN), "--check", *files],
        cwd=PROJECT_ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())

