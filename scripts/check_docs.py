"""Check repository Markdown links without network access or opening private evidence."""

import re
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]


def main():
    failures, count = [], 0
    documents = [ROOT / "README.md", ROOT / "AGENTS.md", *sorted((ROOT / "docs").rglob("*.md"))]
    for document in documents:
        body = document.read_text(encoding="utf-8")
        if len(re.findall(r"^```", body, re.M)) % 2:
            failures.append(f"{document.relative_to(ROOT)}: unclosed code fence")
        for target in re.findall(r"\]\(([^)]+)\)", body):
            target = target.strip("<>")
            if target.startswith(("https://", "http://", "#", "mailto:")):
                continue
            path = Path(unquote(target.split("#", 1)[0]))
            if not path.is_absolute():
                path = document.parent / path
            count += 1
            if not path.exists():
                failures.append(f"{document.relative_to(ROOT)}: {target}")
    for failure in failures:
        print(failure)
    print(f"{len(documents)} documents, {count} local links, {len(failures)} failures")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
