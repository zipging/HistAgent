#!/usr/bin/env python3
"""Download the demo data when needed and start HistAgent Chat."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from download_data import APP_DIR, download_chat_data


def load_env(path: Path, *, overwrite: bool) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if overwrite:
            os.environ[key] = value
        else:
            os.environ.setdefault(key, value)


def main() -> None:
    data_env = APP_DIR / "data.env"
    if not data_env.is_file():
        download_chat_data()
    load_env(data_env, overwrite=False)
    load_env(APP_DIR / ".env", overwrite=True)
    uvicorn.run(
        "server:app",
        app_dir=str(APP_DIR),
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "7860")),
        reload=False,
    )


if __name__ == "__main__":
    main()
