#!/usr/bin/env python3
"""写出 tests/fixtures/text/sample.* 七种文本样例，供 upload/reader 测试引用。

日常 pytest 不依赖本脚本；仅用于重建入库样例。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "tests" / "fixtures" / "text"

SAMPLES: dict[str, str] = {
    "sample.txt": (
        "AgentPlatform text fixture\n"
        "Line two: plain UTF-8 notes.\n"
        "Line three: upload allowlist check.\n"
    ),
    "sample.md": (
        "# Sample Notes\n"
        "\n"
        "- item alpha\n"
        "- item beta\n"
        "\n"
        "A short markdown paragraph for Reader preview.\n"
    ),
    "sample.json": json.dumps(
        {
            "name": "sample",
            "version": 1,
            "tags": ["text", "fixture"],
            "meta": {"source": "generate_text_format_fixtures"},
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n",
    "sample.yaml": (
        "name: sample\n"
        "version: 1\n"
        "tags:\n"
        "  - text\n"
        "  - fixture\n"
        "enabled: true\n"
    ),
    "sample.xml": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<sample>\n"
        "  <name>sample</name>\n"
        "  <version>1</version>\n"
        "  <tag>text</tag>\n"
        "</sample>\n"
    ),
    "sample.html": (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head><meta charset=\"utf-8\"><title>Sample</title></head>\n"
        "<body><h1>Sample HTML</h1><p>fixture preview</p></body>\n"
        "</html>\n"
    ),
    "sample.log": (
        "2026-08-03 10:00:00 INFO boot start\n"
        "2026-08-03 10:00:01 INFO load config ok\n"
        "2026-08-03 10:00:02 WARN retry once\n"
        "2026-08-03 10:00:03 INFO done\n"
    ),
}


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in SAMPLES.items():
        path = out_dir / name
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    args = parser.parse_args()
    paths = generate(args.out_dir)
    for p in paths:
        print(f"wrote {p} ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
