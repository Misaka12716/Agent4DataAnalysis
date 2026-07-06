#!/usr/bin/env python3
"""兼容入口：转发到 scripts/seed_templates.py --acceptance。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    script = Path(__file__).resolve().parent / "seed_templates.py"
    raise SystemExit(subprocess.run([sys.executable, str(script), "--acceptance"], check=False).returncode)
