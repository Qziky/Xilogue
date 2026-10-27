"""An explicitly requested embedding service owns its own loopback port and project model cache."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_process = None
_port = None


def embedding_port(config):
    global _process, _port
    if config.embedding_port:
        return config.embedding_port
    with _lock:
        if _process is not None and _process.poll() is None:
            return _port
        models = (
            Path(config.embedding_models_dir).expanduser().resolve()
            if config.embedding_models_dir
            else Path.cwd() / "data" / "embedding-models"
        )
        if not (models / "provenance.json").is_file():
            raise RuntimeError("project_embedding_model_missing")
        executable = shutil.which("ollama")
        if not executable:
            candidate = (
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"
            )
            if candidate.is_file():
                executable = str(candidate)
        if not executable:
            raise RuntimeError("ollama_not_installed")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            _port = sock.getsockname()[1]
        env = os.environ.copy()
        env.update(
            OLLAMA_HOST=f"127.0.0.1:{_port}",
            OLLAMA_MODELS=str(models),
            OLLAMA_NO_CLOUD="true",
            OLLAMA_NOPRUNE="true",
            OLLAMA_ORIGINS="http://127.0.0.1",
        )
        _process = subprocess.Popen(
            [executable, "serve"],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if _process.poll() is not None:
                break
            connection = http.client.HTTPConnection("127.0.0.1", _port, timeout=0.5)
            try:
                connection.request("GET", "/api/version")
                response = connection.getresponse()
                data = json.loads(response.read(4096))
                if response.status == 200 and isinstance(data.get("version"), str):
                    return _port
            except (OSError, ValueError, http.client.HTTPException):
                pass
            finally:
                connection.close()
            threading.Event().wait(0.1)
        stop_managed_unlocked()
        raise RuntimeError("embedding_service_start_failed")


def stop_managed_unlocked():
    global _process, _port
    if _process is not None and _process.poll() is None:
        _process.terminate()
        try:
            _process.wait(5)
        except subprocess.TimeoutExpired:
            _process.kill()
            _process.wait(5)
    _process = None
    _port = None


def close_managed_services():
    with _lock:
        stop_managed_unlocked()
