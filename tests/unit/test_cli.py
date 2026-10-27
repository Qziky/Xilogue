import subprocess
import sys


def test_check_config_creates_no_data(tmp_path):
    root = tmp_path / "not-created"
    result = subprocess.run(
        [sys.executable, "-m", "xilogue", "--data-dir", str(root), "--check-config"],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0 and b'"version": 1' in result.stdout
    assert not root.exists()


def test_invalid_config_prevents_runtime_initialization(tmp_path):
    config = tmp_path / "invalid.json"
    config.write_text('{"sample_interval_seconds": 0}', encoding="utf-8")
    root = tmp_path / "not-created"
    result = subprocess.run(
        [sys.executable, "-m", "xilogue", "--config", str(config), "--data-dir", str(root)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 2 and not root.exists()
