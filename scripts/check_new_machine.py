"""Verify that a fresh AutoTeams checkout contains its portable handoff assets.

This checks committed files only. It intentionally does not read local secrets,
databases, Docker volumes, or device state.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def check_manifest(path: Path, base: Path, path_key: str) -> int:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    entries = manifest["files"]
    for item in entries:
        target = base / item[path_key]
        if not target.is_file():
            raise ValueError(f"Missing archive file: {target.relative_to(ROOT)}")
        if target.stat().st_size != item["bytes"] or digest(target) != item["sha256"]:
            raise ValueError(f"Archive hash mismatch: {target.relative_to(ROOT)}")
    return len(entries)


def main() -> int:
    required = [
        ".env.example",
        "backend/requirements.lock",
        "backend/alembic.ini",
        "backend/data/demo_runtime_preset.json",
        "frontend/package-lock.json",
        "collaboration-service/package-lock.json",
        "electron/package-lock.json",
        "local-runner/package-lock.json",
        "docs/bp/delin-2026/index.html",
    ]
    missing = [name for name in required if not (ROOT / name).is_file()]
    if missing:
        raise ValueError("Missing checkout files: " + ", ".join(missing))

    bp = ROOT / "docs/bp/delin-2026"
    bp_count = check_manifest(bp / "BP_SYNC_MANIFEST.json", bp, "path")
    archive_count = check_manifest(
        ROOT / "docs/archive/local-workspace/2026-09-30/MANIFEST.json",
        ROOT,
        "repository_path",
    )

    bundles = [
        ROOT / "frontend/public/local-runner/local-runner.zip",
        ROOT / "deploy/cloudflare/local-runner/local-runner.zip",
    ]
    if not all(path.is_file() for path in bundles) or digest(bundles[0]) != digest(bundles[1]):
        raise ValueError("The two Local Runner bundles are missing or differ")
    with ZipFile(bundles[0]) as bundle:
        damaged = bundle.testzip()
        if damaged:
            raise ValueError(f"Damaged Local Runner ZIP member: {damaged}")

    print(f"Portable checkout OK: BP {bp_count}, workspace archive {archive_count}, Runner ZIPs identical")
    if not (ROOT / ".env").exists():
        print("Local configuration is absent: create .env from .env.example and supply your own secrets.")
    print("Live databases, demo credentials, device receipts, and external platform access are not checked.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"Handoff check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
