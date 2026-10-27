"""User-scoped Windows DPAPI secrets, outside config, backups and API responses."""

import base64
import ctypes
import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from ctypes import wintypes

from xilogue.config import api_base_url


class CredentialError(RuntimeError):
    pass


def protect(data: bytes, *, decrypt=False) -> bytes:
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    try:
        crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        buffer = ctypes.create_string_buffer(data)
        source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        target = Blob()
        operation = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(Blob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(Blob),
        ]
        operation.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
            raise CredentialError("credential_protection_failed")
        try:
            return ctypes.string_at(target.data, target.size)
        finally:
            kernel.LocalFree(target.data)
    except (AttributeError, OSError) as exc:
        raise CredentialError("credential_protection_unavailable") from exc


class Credentials:
    def __init__(self, root):
        self.path = root / "ai-credentials.json"
        self.lock = threading.RLock()

    @staticmethod
    def identity(role, base_url):
        return hashlib.sha256((role + ":" + api_base_url(base_url)).encode()).hexdigest()

    def _read(self):
        try:
            if not self.path.exists():
                return {}
            if self.path.stat().st_size > 65536:
                raise ValueError
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()
            ):
                raise ValueError
            return data
        except (OSError, ValueError) as exc:
            raise CredentialError("credential_store_unavailable") from exc

    def update(self, changes):
        with self.lock:
            data = self._read()
            for role, base, value in changes:
                key = self.identity(role, base)
                if value is None:
                    data.pop(key, None)
                else:
                    if (
                        not isinstance(value, str)
                        or not value
                        or len(value) > 4096
                        or any(ord(char) < 33 or ord(char) > 126 for char in value)
                    ):
                        raise ValueError("invalid_api_key")
                    data[key] = base64.b64encode(protect(value.encode())).decode("ascii")
            self._write(data)

    def _write(self, data):
        payload = json.dumps(data)
        if len(payload.encode()) > 65536:
            raise CredentialError("credential_store_full")
        temporary = self.path.with_name(".ai-credentials-" + secrets.token_hex(8))
        try:
            with temporary.open("x", encoding="utf-8") as target:
                target.write(payload)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @contextmanager
    def transaction(self, changes):
        with self.lock:
            previous = self._read() if changes else None
            if changes:
                self.update(changes)
            try:
                yield
            except Exception:
                if previous is not None:
                    self._write(previous)
                raise

    def get(self, config, role):
        prefix = "ai" if role == "analysis" else "embedding"
        with self.lock:
            encoded = self._read().get(self.identity(role, getattr(config, prefix + "_base_url")))
            if encoded is not None:
                try:
                    return protect(base64.b64decode(encoded, validate=True), decrypt=True).decode()
                except (ValueError, UnicodeError) as exc:
                    raise CredentialError("credential_store_unavailable") from exc
        return os.environ.get(getattr(config, prefix + "_api_key_env"), "")

    def configured(self, config, role):
        prefix = "ai" if role == "analysis" else "embedding"
        if getattr(config, prefix + "_provider") == "ollama":
            return False
        with self.lock:
            return self.identity(
                role, getattr(config, prefix + "_base_url")
            ) in self._read() or bool(os.environ.get(getattr(config, prefix + "_api_key_env"), ""))
