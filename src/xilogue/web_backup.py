"""Download a consistent backup only after capture has stopped."""

import shutil
import tempfile
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from xilogue.storage.backup import export_backup


def install_backup_routes(app, runtime):
    @app.get("/api/backup")
    def backup():
        if not runtime._commands.acquire(blocking=False):
            raise HTTPException(409, "command_busy")
        directory = Path(tempfile.mkdtemp(prefix="xilogue-export-"))
        try:
            with runtime.engine._commit:
                if runtime.engine.snapshot()["state"] not in ("stopped", "paused"):
                    raise HTTPException(409, "backup_requires_pause")
                path = directory / "xilogue-backup.zip"
                export_backup(runtime.store, path)
            return FileResponse(
                path,
                filename=path.name,
                media_type="application/zip",
                background=BackgroundTask(shutil.rmtree, directory),
            )
        except BaseException:
            shutil.rmtree(directory)
            raise
        finally:
            runtime._commands.release()
