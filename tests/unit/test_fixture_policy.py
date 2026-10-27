import runpy
from dataclasses import replace
from pathlib import Path
from threading import Event

from xilogue.collector.models import Target


def test_window_identity_alone_does_not_admit_another_document():
    module = runpy.run_path(str(Path(__file__).parents[1] / "manual" / "windows_acceptance.py"))
    allowed = Target(11, 22, 33, "code.exe", "P0-SAFE.txt - Visual Studio Code")
    other = replace(allowed, title="private-document.txt - Visual Studio Code")
    assert allowed == other  # Correct production identity semantics.
    assert module["allowed_window"](allowed, {allowed})
    assert not module["allowed_window"](other, {allowed})
    assert not module["allowed_window"](replace(allowed, process_started=99), {allowed})


def test_test_driver_pumps_ui_while_waiting_for_native_close():
    module = runpy.run_path(str(Path(__file__).parents[1] / "manual" / "windows_acceptance.py"))
    done = Event()

    class Engine:
        def close(self):
            return done

    class Root:
        def update(self):
            done.set()

    assert module["close_with_events"](Engine(), Root(), timeout=0.5)
