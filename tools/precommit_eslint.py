"""Pre-commit hook: run eslint --max-warnings=0 on staged frontend files.

Invoked by .pre-commit-config.yaml with one or more file paths as argv.
Exit 0 if every file passes, non-zero otherwise.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ESLINT_BIN = PROJECT_ROOT / "frontend" / "node_modules" / "eslint" / "bin" / "eslint.js"


def main() -> int:
    if not ESLINT_BIN.exists():
        print(f"eslint not found at {ESLINT_BIN}; did you `npm install` in frontend/?", file=sys.stderr)
        return 1
    node = shutil.which("node")
    if not node:
        print("node executable not found on PATH", file=sys.stderr)
        return 1

    files = [a for a in sys.argv[1:] if a]
    if not files:
        return 0

    result = subprocess.run(
        [node, str(ESLINT_BIN), "--max-warnings=0", *files],
        cwd=PROJECT_ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
