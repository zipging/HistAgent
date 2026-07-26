#!/usr/bin/env python3
"""Build a deterministic SHA-256 manifest for a HistAgent data repository."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--version", default=date.today().isoformat())
    args = parser.parse_args()
    root = args.root.resolve()
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in {".DS_Store", "manifest.json"}:
            continue
        relative = path.relative_to(root).as_posix()
        files.append(
            {
                "path": relative,
                "bytes": path.stat().st_size,
                "sha256": file_sha256(path),
            }
        )
    manifest = {
        "format_version": 1,
        "release": args.version,
        "file_count": len(files),
        "total_bytes": sum(item["bytes"] for item in files),
        "files": files,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(files)} entries to {root / 'manifest.json'}")


if __name__ == "__main__":
    main()
