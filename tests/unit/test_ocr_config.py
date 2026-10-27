import pytest

from xilogue.config import Config, ConfigError


@pytest.mark.parametrize("value", [0, 9, True, 2.5, "2"])
def test_invalid_ocr_threads_are_rejected(value):
    with pytest.raises(ConfigError):
        Config.from_dict({"ocr_threads": value})


def test_existing_version_one_config_gets_ocr_defaults_and_strict_toggle():
    config = Config.from_dict({"version": 1, "sample_interval_seconds": 5})
    assert config.ocr_enabled and config.ocr_threads == 2
    assert not Config.from_dict({"ocr_enabled": False}).ocr_enabled
    with pytest.raises(ConfigError):
        Config.from_dict({"ocr_enabled": "false"})
