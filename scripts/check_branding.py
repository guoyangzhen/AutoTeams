"""Check tracked product names while auditing explicit compatibility exceptions."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
OLD = re.compile(r"auto" + "fde", re.IGNORECASE)


def main() -> int:
    policy = json.loads((ROOT / "docs/branding/LEGACY_IDENTIFIERS.json").read_text(encoding="utf-8"))
    fixed = policy["immutable_migrations"]
    rules = [(rule["paths"], re.compile(rule["pattern"], re.IGNORECASE)) for rule in policy["rules"]]
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode("utf-8").split("\0")
    problems = []
    checked = 0
    exceptions = 0

    def check_text(name: str, payload: bytes) -> None:
        nonlocal exceptions
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError:
            return
        spans = [match.span() for paths, pattern in rules
                 if any(fnmatch.fnmatchcase(name, path) for path in paths)
                 for match in pattern.finditer(source)]
        for hit in OLD.finditer(source):
            if any(start <= hit.start() and hit.end() <= end for start, end in spans):
                exceptions += 1
            else:
                line = source.count("\n", 0, hit.start()) + 1
                problems.append(f"{name}:{line}: unexplained historical identifier")

    for name in filter(None, names):
        path = ROOT / name
        if not path.is_file():
            problems.append(f"Missing tracked file: {name}")
            continue
        if OLD.search(name):
            problems.append(f"Historical product name in path: {name}")
        raw = path.read_bytes()
        checked += 1
        if name in fixed:
            if hashlib.sha256(raw).hexdigest() != fixed[name]:
                problems.append(f"Applied migration changed: {name}")
            continue
        if name == "docs/branding/LEGACY_IDENTIFIERS.json":
            continue  # This file is the explicit, reviewable compatibility policy.
        if path.suffix in (".zip", ".docx", ".xlsx"):
            with ZipFile(path) as bundle:
                for member in bundle.namelist():
                    if OLD.search(member):
                        problems.append(f"Historical name in archive member: {name}!{member}")
                    if member.endswith((".xml", ".json", ".ts", ".js", ".md")):
                        virtual = "local-runner/" + member if name.endswith("local-runner.zip") else name
                        check_text(virtual, bundle.read(member))
        elif b"\0" not in raw:
            check_text(name, raw)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"AutoTeams branding OK: {checked} tracked files; {exceptions} documented compatibility references; {len(fixed)} immutable migrations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
