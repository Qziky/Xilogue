import json

import pytest

from xilogue.config import Config, ConfigError, load_config, runtime_paths


@pytest.mark.parametrize(
    "change",
    [
        {"version": 2},
        {"sample_interval_seconds": 0},
        {"idle_seconds": 0},
        {"candidate_capacity": True},
        {"image_quota_mib": 0},
        {"retention_days": -1},
        {"thumbnail_width": 321},
        {"image_webp_quality": 101},
        {"duplicate_ratio": 0.5},
        {"poll_interval_seconds": float("nan")},
        {"excluded_apps": ["a/b.exe"]},
        {"idle_policy": "auto"},
        {"unknown": 1},
        {"foreground_stabilize_seconds": -1},
    ],
)
def test_invalid_config(change):
    with pytest.raises(ConfigError):
        Config.from_dict(change)


def test_defaults_and_normalization():
    c = Config.from_dict({"excluded_apps": [" Bitwarden.EXE ", "bitwarden.exe"]})
    assert c.excluded_apps == ("bitwarden.exe",)
    assert Config.from_dict(json.loads(json.dumps(c.to_dict()))) == c
    assert Config.from_dict({"idle_policy": "ignore"}).idle_policy == "ignore"


def test_paths_do_not_create_data(tmp_path):
    root, config = runtime_paths(str(tmp_path / "new"), None)
    assert config == root / "config.json"
    assert not root.exists()


def test_bad_json_is_readable(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSONDecodeError"):
        load_config(path)


@pytest.mark.parametrize(
    "change",
    [
        {"poll_interval_seconds": 1e308},
        {"sample_interval_seconds": 10**500},
        {"retention_days": 10**20},
        {"image_quota_mib": 10**20},
    ],
)
def test_large_values_fail_before_worker_start(change):
    with pytest.raises(ConfigError):
        Config.from_dict(change)
