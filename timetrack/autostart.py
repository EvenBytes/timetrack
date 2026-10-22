"""Arranque con Windows: una entrada en HKCU\\...\\Run, solo para el usuario actual.

No necesita permisos de administrador. Lo usan el menú del icono y el panel web.
"""

import logging
import sys
import winreg
from pathlib import Path

log = logging.getLogger("timetrack")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "timetrack"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # En desarrollo: pythonw (sin consola) ejecutando el paquete desde la carpeta del proyecto.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    root = Path(__file__).resolve().parent.parent
    return f'cmd /c start "" /d "{root}" "{pythonw}" -m timetrack app'


def _registered() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            return winreg.QueryValueEx(key, _RUN_VALUE)[0]
    except FileNotFoundError:
        return None


def autostart_enabled() -> bool:
    return _registered() is not None


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, _RUN_VALUE, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, _RUN_VALUE)
            except FileNotFoundError:
                pass
    log.info("Inicio con Windows %s", "activado" if enabled else "desactivado")


def refresh_autostart() -> None:
    """Si está activado pero apunta a otro sitio (se movió la carpeta o se actualizó el .exe en
    otra ruta), lo vuelve a apuntar a este ejecutable; si no, Windows no lo encontraría."""
    current = _registered()
    if current is not None and current != _launch_command():
        set_autostart(True)
        log.info("Inicio con Windows actualizado a la nueva ubicación")
