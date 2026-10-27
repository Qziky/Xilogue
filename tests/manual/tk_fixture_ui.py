"""Small controls for synthetic Tk capture fixtures only; not the product UI."""

import tkinter as tk
from tkinter import messagebox, ttk

from xilogue.config import load_config


class FixtureControls:
    def __init__(self, root, engine, store, config_path):
        root.title("昔录 · Xilogue · 合成窗口测试控制")
        root.geometry("740x260")
        panel = ttk.Frame(root, padding=20)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="仅用于合成窗口验收；正式入口已迁移 Web UI").pack()
        ttk.Button(panel, text="开始 / 恢复", command=engine.start).pack(side="left")
        ttk.Button(panel, text="暂停", command=engine.pause).pack(side="left", padx=8)

        def reload():
            try:
                engine.reload(load_config(config_path))
            except (ValueError, RuntimeError) as exc:
                messagebox.showerror("配置错误", str(exc), parent=root)

        ttk.Button(panel, text="重新加载配置", command=reload).pack(side="left")
        ttk.Button(panel, text="退出", command=engine.close).pack(side="right")
        value = tk.StringVar()
        ttk.Label(root, textvariable=value, padding=20).pack()
        root.protocol("WM_DELETE_WINDOW", engine.close)

        def refresh():
            snap = engine.snapshot()
            value.set(f"{snap['state']} · {snap['reason']} · {snap['counts']}")
            if snap["state"] == "closed":
                root.quit()
            else:
                root.after(100, refresh)

        refresh()
