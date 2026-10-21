"""Acceso mínimo a Win32 vía ctypes: ventana activa, proceso, inactividad y DPI."""

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GA_ROOTOWNER = 3
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


@dataclass(frozen=True)
class ForegroundWindow:
    hwnd: int
    pid: int
    process: str      # p. ej. "Code.exe"
    exe_path: str
    title: str
    minimized: bool
    class_name: str = ""


def enable_dpi_awareness() -> None:
    """Sin esto, en pantallas escaladas los rectángulos y las capturas no coinciden."""
    try:
        user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError):
        ctypes.windll.shcore.SetProcessDpiAwareness(2)


def _window_title(hwnd) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _process_image(pid: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def get_foreground_window() -> ForegroundWindow | None:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe_path = _process_image(pid.value)
    title = _window_title(hwnd)
    if not title.strip():
        # Menús, avisos y desplegables de los navegadores son ventanas sin título: se usa la
        # ventana principal a la que pertenecen (también para la captura del OCR).
        hwnd, title = _titled_window_for(hwnd, pid.value)
    return ForegroundWindow(
        hwnd=hwnd,
        pid=pid.value,
        process=os.path.basename(exe_path) or "desconocido",
        exe_path=exe_path,
        title=title,
        minimized=bool(user32.IsIconic(hwnd)),
        class_name=_class_name(hwnd),
    )


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _titled_window_for(hwnd, pid: int):
    owner = user32.GetAncestor(hwnd, GA_ROOTOWNER)
    if owner and owner != hwnd:
        title = _window_title(owner)
        if title.strip():
            return owner, title

    # Sin propietario: la ventana visible con título más grande del mismo proceso.
    best = (hwnd, "", 0)

    @WNDENUMPROC
    def visit(candidate, _):
        nonlocal best
        if not user32.IsWindowVisible(candidate) or user32.IsIconic(candidate):
            return True
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(candidate, ctypes.byref(owner_pid))
        if owner_pid.value != pid:
            return True
        title = _window_title(candidate)
        rect = wintypes.RECT()
        if title.strip() and user32.GetWindowRect(candidate, ctypes.byref(rect)):
            area = (rect.right - rect.left) * (rect.bottom - rect.top)
            if area > best[2]:
                best = (candidate, title, area)
        return True

    user32.EnumWindows(visit, 0)
    return best[0], best[1]


def get_window_rect(hwnd) -> tuple[int, int, int, int] | None:
    """Rectángulo visible (sin los bordes invisibles de Windows 10/11): left, top, right, bottom."""
    rect = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(rect), ctypes.sizeof(rect))
    if hr != 0 and not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
        return None
    return rect.left, rect.top, rect.right, rect.bottom


def seconds_since_last_input() -> float:
    info = LASTINPUTINFO(cbSize=ctypes.sizeof(LASTINPUTINFO))
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 0.0
    # Ambos contadores son de 32 bits y dan la vuelta cada ~49 días.
    elapsed_ms = (kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF
    return elapsed_ms / 1000.0
