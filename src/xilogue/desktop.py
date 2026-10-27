"""Windows tray controls and explicitly configured per-user login startup."""

import queue
import subprocess
import sys
import threading
import webbrowser
from importlib.resources import files


def startup(action, data_dir, config_path):
    import winreg

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        key = (
            winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            if action == "enable"
            else winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_READ if action == "status" else winreg.KEY_SET_VALUE | winreg.KEY_READ,
            )
        )
    except FileNotFoundError:
        return {"enabled": False, "command": None, "capture_on_start": False}
    with key:
        if action == "enable":
            command = (
                [sys.executable]
                if getattr(sys, "frozen", False)
                else [sys.executable, "-m", "xilogue"]
            )
            command += ["--no-browser", "--data-dir", str(data_dir), "--config", str(config_path)]
            winreg.SetValueEx(key, "Xilogue", 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        elif action == "disable":
            try:
                winreg.DeleteValue(key, "Xilogue")
            except FileNotFoundError:
                pass
        try:
            value, _ = winreg.QueryValueEx(key, "Xilogue")
        except FileNotFoundError:
            value = None
    return {"enabled": value is not None, "command": value, "capture_on_start": False}


class Tray:
    def __init__(self, runtime):
        import pystray
        from PIL import Image

        self.runtime = runtime
        self.commands = queue.Queue(4)
        self.stop = threading.Event()
        with files("xilogue").joinpath("assets", "icon-64.png").open("rb") as icon_file:
            image = Image.open(icon_file).convert("RGBA")
        self.icon = pystray.Icon(
            "Xilogue",
            image,
            "昔录 · Xilogue · 默认停止",
            menu=pystray.Menu(
                pystray.MenuItem(
                    "打开本地界面", lambda: webbrowser.open(runtime.url), default=True
                ),
                pystray.MenuItem(
                    "开始 / 恢复采集",
                    lambda: self.submit("start"),
                    enabled=lambda item: (
                        runtime.engine.snapshot()["state"] in ("stopped", "paused")
                    ),
                ),
                pystray.MenuItem("暂停采集", lambda: self.submit("pause")),
                pystray.MenuItem("退出程序", lambda: self.submit("exit")),
            ),
        )
        self.thread = threading.Thread(target=self.icon.run, name="memory-tray")
        self.worker = threading.Thread(target=self.run, name="memory-tray-commands")
        self.worker.start()
        self.thread.start()

    def submit(self, action):
        try:
            self.commands.put_nowait(action)
        except queue.Full:
            self.runtime.engine._event("error", "tray_command_queue_full")

    def run(self):
        while not self.stop.is_set():
            try:
                action = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.runtime.control(action)
            except Exception:
                self.runtime.engine._event("error", "tray_command_failed")

    def close(self):
        self.stop.set()
        self.icon.stop()
        self.worker.join(3)
        self.thread.join(3)

    def snapshot(self):
        return {
            "alive": self.thread.is_alive(),
            "commands_alive": self.worker.is_alive(),
            "queued": self.commands.qsize(),
        }
