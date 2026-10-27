import numpy as np

from xilogue.config import Config
from xilogue.processing.change import compare, prepare

C = Config()


def image(value=128):
    return np.full((180, 320, 3), value, np.uint8)


def test_first_identical_and_black():
    p = prepare(image(), C)
    assert compare(p, None, C).reason == "first_frame"
    assert compare(p, None, C).metrics["parameters"]["duplicate_mae"] == C.duplicate_mae
    assert not compare(p, p, C).keep
    assert compare(prepare(image(0), C), None, C).reason == "suspected_black_frame"


def test_local_and_large_change():
    base = prepare(image(), C)
    changed = image()
    changed[40:44, 40:60] = 255
    result = compare(prepare(changed, C), base, C)
    assert result.keep and result.reason == "local_change"
    assert result.metrics["changed_ratio"] < 0.003
    assert compare(prepare(image(255), C), base, C).reason == "large_change"


def test_accumulation_uses_saved_reference():
    base = prepare(image(), C)
    for difference in [1, 2, 3]:
        changed = image()
        changed[:20, :20] = 128 + difference
        result = compare(prepare(changed, C), base, C)
        assert result.keep == (difference == 3)


def test_dimensions_force_new_reference():
    base = prepare(image(), C)
    other = prepare(np.full((300, 400, 3), 128, np.uint8), C)
    assert compare(other, base, C).reason == "dimensions_changed"
