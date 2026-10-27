import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def test_isolated_install_update_and_uninstall_preserve_user_data(tmp_path):
    # Only the test child sees this LOCALAPPDATA; no shortcut or startup write is performed.
    project = Path(__file__).resolve().parents[2]
    source = tmp_path / "package"
    source.mkdir()
    shutil.copytree(project / "scripts/distribution", source / "maintenance")
    (source / "Xilogue.exe").write_text("synthetic v1", encoding="utf-8")
    (source / "build-info.json").write_text('{"signed":false}', encoding="utf-8")

    def manifest():
        hashes = {
            p.relative_to(source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob("*")
            if p.is_file() and p.name != "package-files.json"
        }
        (source / "package-files.json").write_text(json.dumps(hashes), encoding="utf-8")

    manifest()
    local = tmp_path / "isolated-localappdata"
    local.mkdir()
    env = os.environ | {"LOCALAPPDATA": str(local)}

    def run(path, *args):
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(path),
                *args,
                "-NoShortcut",
            ],
            env=env,
            capture_output=True,
            timeout=30,
            text=True,
        )
        assert result.returncode == 0, result.stderr

    run(source / "maintenance/install.ps1")
    installed = local / "Programs/Xilogue"
    assert (installed / "Xilogue.exe").read_text() == "synthetic v1"
    data = local / "Xilogue"
    data.mkdir()
    (data / ".xilogue").write_text("Xilogue/v1\n", encoding="utf-8")
    (data / "keep.txt").write_text("synthetic private data", encoding="utf-8")
    (source / "Xilogue.exe").write_text("synthetic v2", encoding="utf-8")
    manifest()
    run(source / "maintenance/install.ps1", "-Update")
    assert (installed / "Xilogue.exe").read_text() == "synthetic v2"
    assert (local / "Programs/Xilogue.previous/Xilogue.exe").read_text() == "synthetic v1"
    run(installed / "maintenance/uninstall.ps1")
    assert not installed.exists() and (data / "keep.txt").exists()
    (source / "Xilogue.exe").write_text("tampered", encoding="utf-8")
    env["LOCALAPPDATA"] = str(tmp_path / "rejected-install")
    rejected = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(source / "maintenance/install.ps1"),
            "-NoShortcut",
        ],
        env=env,
        capture_output=True,
        timeout=30,
        text=True,
    )
    assert rejected.returncode != 0 and "hash mismatch" in rejected.stderr
    assert not (tmp_path / "rejected-install/Programs/Xilogue").exists()
