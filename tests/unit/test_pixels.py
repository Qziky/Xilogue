import numpy as np
import pytest

from xilogue.collector.backend import owned_bgr


def test_padded_rows_and_buffer_lifetime():
    raw = np.zeros((4, 32), np.uint8)
    view = raw[:, :20].reshape(4, 5, 4)
    view[1, 2] = [0, 0, 255, 255]
    image = owned_bgr(view, 5, 4)
    raw[:] = 0
    assert image[1, 2].tolist() == [0, 0, 255]
    assert image.flags.c_contiguous and image.flags.owndata


def test_bad_shape():
    with pytest.raises(ValueError):
        owned_bgr(np.zeros((3, 3, 3), np.uint8), 3, 3)


def test_backend_failed_stop_preserves_control():
    from xilogue.collector.backend import WgcBackend

    class Control:
        def stop(self):
            raise RuntimeError("injected")

    backend = WgcBackend()
    control = Control()
    backend.control = control
    backend.closed.clear()
    with pytest.raises(RuntimeError):
        backend.stop()
    assert backend.control is control and not backend.closed.is_set()
