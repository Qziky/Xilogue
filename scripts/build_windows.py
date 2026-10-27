"""Reproducible-input, timestamped Windows portable package; never includes user data/models."""

import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    if sys.platform != "win32":
        raise RuntimeError("windows_build_required")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    output = ROOT / "dist" / stamp
    output.mkdir(parents=True)
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onedir",
        "--console",
        "--noupx",
        "--name",
        "Xilogue",
        "--icon",
        str(ROOT / "src" / "xilogue" / "assets" / "icon.ico"),
        "--distpath",
        str(output),
        "--workpath",
        str(output / "work"),
        "--specpath",
        str(output / "spec"),
        "--paths",
        str(ROOT / "src"),
        "--collect-data",
        "xilogue",
        "--collect-all",
        "rapidocr_onnxruntime",
        "--collect-all",
        "windows_capture",
        "--collect-all",
        "pystray",
        "--hidden-import",
        "uvicorn.logging",
        "--hidden-import",
        "uvicorn.loops.auto",
        "--hidden-import",
        "uvicorn.protocols.http.auto",
        "--hidden-import",
        "uvicorn.lifespan.on",
        str(ROOT / "scripts" / "frozen_entry.py"),
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    package = output / "Xilogue"
    shutil.copy2(ROOT / "LICENSE", package / "LICENSE")
    shutil.copytree(ROOT / "scripts" / "distribution", package / "maintenance")
    shutil.copytree(ROOT / "docs", package / "docs")
    shutil.copy2(ROOT / "uv.lock", package / "uv.lock")
    shutil.copytree(
        ROOT / "src",
        package / "source" / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        ROOT / "scripts",
        package / "source" / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    shutil.copytree(
        ROOT / "frontend",
        package / "source" / "frontend",
        ignore=shutil.ignore_patterns("node_modules", ".vite", "coverage"),
    )
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, package / "source" / name)
    packages = {}
    notices = package / "licenses"
    notices.mkdir()
    for dist in importlib.metadata.distributions():
        name = dist.metadata["Name"]
        packages[name] = {
            "version": dist.version,
            "license": dist.metadata.get("License-Expression")
            or dist.metadata.get("License", "See bundled license files"),
        }
        if name.casefold() == "pystray":
            import pystray

            shutil.copytree(
                Path(pystray.__file__).parent,
                notices / "pystray" / "source",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        for item in dist.files or []:
            if "dist-info" in str(item) and (
                "license" in str(item).casefold() or "notice" in str(item).casefold()
            ):
                path = Path(dist.locate_file(item))
                if path.is_file():
                    target = notices / name / str(item).split("dist-info", 1)[1].lstrip("/\\")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, target)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = bool(
        subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    )
    (package / "build-info.json").write_text(
        json.dumps(
            {
                "version": importlib.metadata.version("xilogue"),
                "revision": revision,
                "dirty": dirty,
                "built_at_utc": stamp,
                "platform": sys.platform,
                "python": sys.version,
                "signed": False,
                "packages": packages,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {}
    for file in package.rglob("*"):
        if file.is_file():
            with file.open("rb") as source:
                manifest[file.relative_to(package).as_posix()] = hashlib.file_digest(
                    source, "sha256"
                ).hexdigest()
    (package / "package-files.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    archive = Path(
        shutil.make_archive(str(output / "Xilogue-windows-x64"), "zip", output, package.name)
    )
    with archive.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    (output / "SHA256SUMS.txt").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    print(json.dumps({"package": str(package), "zip": str(archive), "sha256": digest}))


if __name__ == "__main__":
    main()
