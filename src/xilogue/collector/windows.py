"""Small Win32 boundary. Unknown or inaccessible context is never allowed."""

from __future__ import annotations

import ctypes as C
import os
from ctypes import wintypes as W

from xilogue.collector.models import Target

user = C.WinDLL("user32", use_last_error=True)
kernel = C.WinDLL("kernel32", use_last_error=True)
wts = C.WinDLL("wtsapi32", use_last_error=True)


def bind(dll, name, restype, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = restype, args
    return fn


bind(user, "GetForegroundWindow", W.HWND)
bind(user, "IsWindow", W.BOOL, W.HWND)
bind(user, "IsWindowVisible", W.BOOL, W.HWND)
bind(user, "IsIconic", W.BOOL, W.HWND)
bind(user, "GetWindowThreadProcessId", W.DWORD, W.HWND, C.POINTER(W.DWORD))
bind(user, "GetWindowTextLengthW", C.c_int, W.HWND)
bind(user, "GetWindowTextW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
bind(kernel, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
bind(kernel, "CloseHandle", W.BOOL, W.HANDLE)
bind(kernel, "GetProcessTimes", W.BOOL, W.HANDLE, *([C.POINTER(W.FILETIME)] * 4))
bind(kernel, "QueryFullProcessImageNameW", W.BOOL, W.HANDLE, W.DWORD, W.LPWSTR, C.POINTER(W.DWORD))
bind(user, "OpenInputDesktop", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
bind(user, "CloseDesktop", W.BOOL, W.HANDLE)
bind(
    user,
    "GetUserObjectInformationW",
    W.BOOL,
    W.HANDLE,
    C.c_int,
    W.LPVOID,
    W.DWORD,
    C.POINTER(W.DWORD),
)
bind(kernel, "ProcessIdToSessionId", W.BOOL, W.DWORD, C.POINTER(W.DWORD))
bind(
    wts,
    "WTSQuerySessionInformationW",
    W.BOOL,
    W.HANDLE,
    W.DWORD,
    C.c_int,
    C.POINTER(W.LPVOID),
    C.POINTER(W.DWORD),
)
bind(wts, "WTSFreeMemory", None, W.LPVOID)
bind(kernel, "GetTickCount64", C.c_ulonglong)


class LastInput(C.Structure):
    _fields_ = [("cbSize", W.UINT), ("dwTime", W.DWORD)]


bind(user, "GetLastInputInfo", W.BOOL, C.POINTER(LastInput))


def target_for(hwnd: int) -> Target | None:
    if not hwnd or not user.IsWindow(hwnd) or not user.IsWindowVisible(hwnd):
        return None
    if user.IsIconic(hwnd):
        return None
    pid = W.DWORD()
    if not user.GetWindowThreadProcessId(hwnd, C.byref(pid)):
        return None
    handle = kernel.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return None
    try:
        path = C.create_unicode_buffer(32768)
        size = W.DWORD(len(path))
        times = [W.FILETIME() for _ in range(4)]
        if not kernel.QueryFullProcessImageNameW(handle, 0, path, C.byref(size)):
            return None
        if not kernel.GetProcessTimes(handle, *(C.byref(x) for x in times)):
            return None
        title = C.create_unicode_buffer(max(1, user.GetWindowTextLengthW(hwnd) + 1))
        if not user.GetWindowTextW(hwnd, title, len(title)) or not title.value.strip():
            return None
        start = times[0].dwLowDateTime | times[0].dwHighDateTime << 32
        return Target(
            int(hwnd), pid.value, start, os.path.basename(path.value).casefold(), title.value
        )
    finally:
        kernel.CloseHandle(handle)


class WindowsMonitor:
    def foreground(self) -> Target | None:
        return target_for(user.GetForegroundWindow())

    def desktop_available(self) -> bool:
        # Require an active, unlocked session AND a readable normal input desktop.
        sid = W.DWORD()
        if not kernel.ProcessIdToSessionId(os.getpid(), C.byref(sid)):
            return False
        pointer, size = W.LPVOID(), W.DWORD()
        if not wts.WTSQuerySessionInformationW(
            None, sid.value, 25, C.byref(pointer), C.byref(size)
        ):
            return False
        try:
            # WTSINFOEX: DWORD Level + alignment padding + Level1's first 3 fields.
            if size.value < 20:
                return False
            values = (W.DWORD * 5).from_address(pointer.value)
            if values[0] != 1 or values[2] != sid.value:
                return False
            if values[3] != 0 or values[4] != 1:  # WTSActive; unlocked (Win10/11).
                return False
        finally:
            wts.WTSFreeMemory(pointer)
        desktop = user.OpenInputDesktop(0, False, 0x0001)
        if not desktop:
            return False
        try:
            name, needed = C.create_unicode_buffer(256), W.DWORD()
            return bool(
                user.GetUserObjectInformationW(desktop, 2, name, C.sizeof(name), C.byref(needed))
                and name.value.casefold() == "default"
            )
        finally:
            user.CloseDesktop(desktop)

    def idle_seconds(self) -> float:
        info = LastInput(C.sizeof(LastInput), 0)
        if not user.GetLastInputInfo(C.byref(info)):
            return float("inf")
        return ((kernel.GetTickCount64() - info.dwTime) & 0xFFFFFFFF) / 1000
