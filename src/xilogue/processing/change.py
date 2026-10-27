"""Explainable visual filtering. The caller advances reference only after commit."""

from dataclasses import dataclass

import cv2
import numpy as np

from xilogue.config import Config


@dataclass(frozen=True)
class Preview:
    gray: np.ndarray
    width: int
    height: int


@dataclass(frozen=True)
class Decision:
    keep: bool
    reason: str
    metrics: dict


def prepare(pixels: np.ndarray, config: Config) -> Preview:
    if pixels.dtype != np.uint8 or pixels.ndim != 3 or pixels.shape[2] != 3:
        raise ValueError("invalid_bgr_image")
    height, width = pixels.shape[:2]
    if min(height, width) < 2:
        raise ValueError("invalid_dimensions")
    gray = cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(
        gray, (config.thumbnail_width, config.thumbnail_height), interpolation=cv2.INTER_AREA
    )
    return Preview(gray, width, height)


def compare(current: Preview, reference: Preview | None, config: Config) -> Decision:
    metrics = {
        "parameter_version": 1,
        "parameters": {
            key: getattr(config, key)
            for key in (
                "thumbnail_width",
                "thumbnail_height",
                "grid_columns",
                "grid_rows",
                "pixel_difference",
                "duplicate_mae",
                "duplicate_ratio",
                "duplicate_tile_mae",
                "large_change_ratio",
            )
        },
    }
    if int(current.gray.max()) <= 3:
        # A conservative diagnostic, not a claim to detect DRM or private content.
        return Decision(False, "suspected_black_frame", metrics)
    if reference is None:
        return Decision(True, "first_frame", metrics)
    if (current.width, current.height) != (reference.width, reference.height):
        return Decision(True, "dimensions_changed", metrics)
    delta = np.abs(current.gray.astype(np.int16) - reference.gray.astype(np.int16))
    mae = float(delta.mean())
    ratio = float((delta > config.pixel_difference).mean())
    tiles = delta.reshape(
        config.grid_rows,
        config.thumbnail_height // config.grid_rows,
        config.grid_columns,
        config.thumbnail_width // config.grid_columns,
    )
    tile = float(tiles.mean(axis=(1, 3)).max())
    metrics.update(mae=mae, changed_ratio=ratio, max_tile_mae=tile)
    if (
        mae < config.duplicate_mae
        and ratio < config.duplicate_ratio
        and tile < config.duplicate_tile_mae
    ):
        return Decision(False, "near_duplicate", metrics)
    reason = (
        "large_change"
        if ratio >= config.large_change_ratio
        else "local_change"
        if tile >= config.duplicate_tile_mae
        else "global_change"
    )
    return Decision(True, reason, metrics)
