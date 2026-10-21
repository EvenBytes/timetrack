"""¿Hay una llamada en curso? Micrófono o cámara en uso por cualquier aplicación.

Windows anota en el registro del usuario (CapabilityAccessManager\\ConsentStore) cada uso del
micrófono y de la cámara: mientras una aplicación los tiene abiertos, LastUsedTimeStop vale 0.
Es lo que usa el propio icono de privacidad de la barra de tareas; se lee en local y sin permisos.
"""

import logging
import winreg

log = logging.getLogger("timetrack")

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore"
_DEVICES = ("microphone", "webcam")


def media_in_use() -> bool:
    """True si alguna aplicación está usando ahora el micrófono o la cámara."""
    for device in _DEVICES:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{_BASE}\{device}") as key:
                if _any_in_use(key):
                    return True
        except OSError:
            continue
    return False


def _any_in_use(key, depth: int = 0) -> bool:
    # Apps empaquetadas: una subclave por app. Las de escritorio (navegadores, Zoom…) van un
    # nivel más abajo, en NonPackaged\<ruta del exe>.
    if _in_use(key):
        return True
    if depth >= 2:
        return False
    i = 0
    while True:
        try:
            name = winreg.EnumKey(key, i)
        except OSError:
            return False
        i += 1
        try:
            with winreg.OpenKey(key, name) as sub:
                if _any_in_use(sub, depth + 1):
                    return True
        except OSError:
            continue


def _in_use(key) -> bool:
    try:
        start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
        stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
    except OSError:
        return False
    return bool(start) and stop == 0
