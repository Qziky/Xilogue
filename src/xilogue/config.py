"""Versioned, strict configuration; loading never starts capture."""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path


class ConfigError(ValueError):
    """A readable configuration error safe to show before capture starts."""


@dataclass(frozen=True)
class Config:
    version: int = 1
    sample_interval_seconds: float = 5.0
    foreground_stabilize_seconds: float = 0.6
    poll_interval_seconds: float = 0.25
    idle_policy: str = "pause"
    idle_seconds: float = 90.0
    image_quota_mib: int = 512
    retention_days: int = 7
    image_webp_quality: int = 78
    thumbnail_width: int = 320
    thumbnail_height: int = 180
    grid_columns: int = 16
    grid_rows: int = 9
    pixel_difference: int = 14
    duplicate_mae: float = 0.8
    duplicate_ratio: float = 0.003
    duplicate_tile_mae: float = 3.0
    large_change_ratio: float = 0.04
    candidate_capacity: int = 2
    event_capacity: int = 300
    maintenance_seconds: float = 60.0
    ocr_enabled: bool = True
    ocr_threads: int = 2
    session_gap_seconds: float = 300.0
    session_max_seconds: float = 3600.0
    session_title_similarity: float = 0.65
    ai_enabled: bool = False
    ai_model: str = "recorder-vision:balanced"
    ai_timeout_seconds: float = 120.0
    ai_stable_seconds: float = 60.0
    review_timezone: str = "Asia/Shanghai"
    embedding_model: str = "embeddinggemma:latest"
    semantic_enabled: bool = False
    embedding_port: int = 0
    embedding_models_dir: str = ""
    excluded_apps: tuple[str, ...] = (
        "1password.exe",
        "bitwarden.exe",
        "keepass.exe",
        "keepassxc.exe",
        "passwordmanager.exe",
    )

    @classmethod
    def from_dict(cls, data: object) -> Config:
        if not isinstance(data, dict):
            raise ConfigError("配置必须为 JSON 对象")
        defaults = asdict(cls())
        unknown = set(data) - defaults.keys()
        if unknown:
            raise ConfigError("未知配置项: " + ", ".join(sorted(unknown)))
        values = defaults | data
        for key, default in defaults.items():
            value = values[key]
            if key == "excluded_apps":
                if not isinstance(value, (list, tuple)) or any(
                    not isinstance(x, str) or not x.strip() or "/" in x or "\\" in x for x in value
                ):
                    raise ConfigError("excluded_apps 必须为非空进程名字符串数组")
                values[key] = tuple(sorted({x.strip().casefold() for x in value}))
            elif isinstance(default, float):
                try:
                    finite = type(value) in (int, float) and math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ConfigError(f"{key} 必须为有限数字")
            elif type(value) is not type(default):
                raise ConfigError(f"{key} 类型错误")
        c = cls(**values)
        if c.version != 1:
            raise ConfigError("不支持的配置版本；需要 version=1")
        for key in (
            "sample_interval_seconds",
            "poll_interval_seconds",
            "idle_seconds",
            "maintenance_seconds",
            "session_gap_seconds",
            "session_max_seconds",
            "image_quota_mib",
            "retention_days",
            "thumbnail_width",
            "thumbnail_height",
            "grid_columns",
            "grid_rows",
            "candidate_capacity",
            "event_capacity",
        ):
            if getattr(c, key) <= 0:
                raise ConfigError(f"{key} 必须大于 0")
        if c.foreground_stabilize_seconds < 0 or c.idle_policy not in ("pause", "ignore"):
            raise ConfigError("稳定等待不可为负；idle_policy 为 pause 或 ignore")
        for field in fields(c):
            if field.name.endswith("_seconds") and getattr(c, field.name) > 31_536_000:
                raise ConfigError(f"{field.name} 不可超过一年")
        if c.retention_days > 36500 or c.image_quota_mib > 1_048_576:
            raise ConfigError("保留期限最多 36500 天；图像配额最多 1048576 MiB")
        if not 0 <= c.image_webp_quality <= 100 or not 0 <= c.pixel_difference <= 255:
            raise ConfigError("WebP 质量范围 0–100，灰度阈值范围 0–255")
        for key in ("duplicate_mae", "duplicate_tile_mae"):
            if not 0 <= getattr(c, key) <= 255:
                raise ConfigError(f"{key} 范围为 0–255")
        if not 0 <= c.duplicate_ratio <= c.large_change_ratio <= 1:
            raise ConfigError("变化比例需要 0 <= duplicate_ratio <= large_change_ratio <= 1")
        if c.thumbnail_width % c.grid_columns or c.thumbnail_height % c.grid_rows:
            raise ConfigError("缩略图尺寸必须能被网格数整除")
        if c.thumbnail_width * c.thumbnail_height > 4_000_000:
            raise ConfigError("缩略图过大")
        if c.candidate_capacity > 32 or c.event_capacity > 10000:
            raise ConfigError("候选队列最多 32，事件队列最多 10000")
        if not 1 <= c.ocr_threads <= 8:
            raise ConfigError("ocr_threads 范围为 1–8")
        if (
            not 0 <= c.session_title_similarity <= 1
            or c.session_gap_seconds > c.session_max_seconds
        ):
            raise ConfigError("标题相似度范围 0–1，分组间隔不能大于片段跨度上限")
        if not 5 <= c.ai_timeout_seconds <= 600:
            raise ConfigError("ai_timeout_seconds 范围为 5–600")
        if not 5 <= c.ai_stable_seconds <= 3600:
            raise ConfigError("ai_stable_seconds 范围为 5–3600")
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(c.review_timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ConfigError("review_timezone 必须为有效 IANA 时区") from exc
        for model in (c.ai_model, c.embedding_model):
            if not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", model) or ":cloud" in model:
                raise ConfigError("模型名必须为本地 Ollama 模型标识")
        if c.embedding_port not in (0, 11434, 11435):
            raise ConfigError("embedding_port 为 0（独立服务）或 11434/11435（已有本地服务）")
        if len(c.embedding_models_dir) > 4096 or "\0" in c.embedding_models_dir:
            raise ConfigError("embedding_models_dir 路径无效")
        return c

    def to_dict(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


def load_config(path: Path) -> Config:
    try:
        return Config.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取配置（{type(exc).__name__}）") from exc


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        raise ConfigError("未找到 LOCALAPPDATA；请显式指定 --data-dir")
    return Path(base) / "Xilogue"


def runtime_paths(data_dir: str | None, config: str | None) -> tuple[Path, Path]:
    root = Path(data_dir).expanduser().resolve() if data_dir else default_data_dir().resolve()
    config_path = Path(config).expanduser().resolve() if config else root / "config.json"
    return root, config_path
