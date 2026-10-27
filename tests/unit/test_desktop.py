import sys
import types

from xilogue.desktop import startup


def test_startup_is_explicit_per_user_and_never_starts_capture(monkeypatch, tmp_path):
    calls, values = [], {}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def query(key, name):
        if name not in values:
            raise FileNotFoundError
        return values[name], 1

    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER="current-user",
        KEY_READ=1,
        KEY_SET_VALUE=2,
        REG_SZ=1,
        CreateKey=lambda *args: calls.append(("create", args)) or Key(),
        OpenKey=lambda *args: calls.append(("open", args)) or Key(),
        SetValueEx=lambda key, name, reserved, kind, value: values.update({name: value}),
        QueryValueEx=query,
        DeleteValue=lambda key, name: values.pop(name),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    assert not startup("status", tmp_path, tmp_path / "config.json")["enabled"]
    assert calls[0][0] == "open"  # Read-only status must not create registry keys.
    result = startup("enable", tmp_path, tmp_path / "config.json")
    assert result["enabled"] and not result["capture_on_start"]
    assert "--no-browser" in result["command"] and "--data-dir" in result["command"]
    assert not startup("disable", tmp_path, tmp_path / "config.json")["enabled"]
