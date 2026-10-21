"""URL de la pestaña activa de los navegadores, leída de la barra de direcciones con UI Automation
(la accesibilidad de Windows): sin extensiones y sin tocar el navegador.

Se guarda solo dominio + ruta, sin parámetros ni fragmento (ahí suelen ir tokens y búsquedas).
"""

import logging
import re
from urllib.parse import urlsplit

log = logging.getLogger("timetrack")

BROWSERS = {"chrome.exe", "msedge.exe", "brave.exe", "vivaldi.exe", "opera.exe", "firefox.exe"}
_HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)


class BrowserUrls:
    """Debe crearse y usarse en el mismo hilo (COM)."""

    def __init__(self) -> None:
        import comtypes
        import comtypes.client

        comtypes.CoInitialize()
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uia

        self._uia_mod = uia
        self._uia = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
        # La barra de direcciones es el primer cuadro de texto de la ventana en Chrome/Edge;
        # en Firefox tiene un AutomationId propio.
        edit = self._uia.CreatePropertyCondition(uia.UIA_ControlTypePropertyId,
                                                 uia.UIA_EditControlTypeId)
        self._conditions = {
            "firefox.exe": self._uia.CreateAndCondition(
                edit, self._uia.CreatePropertyCondition(uia.UIA_AutomationIdPropertyId,
                                                        "urlbar-input")),
        }
        self._default = edit

    def url(self, hwnd: int, process: str) -> str | None:
        """'' si no hay URL; None si no se sabe (se está escribiendo en la barra)."""
        process = process.lower()
        if process not in BROWSERS:
            return ""
        uia = self._uia_mod
        try:
            root = self._uia.ElementFromHandle(hwnd)
            box = root.FindFirst(uia.TreeScope_Descendants,
                                 self._conditions.get(process, self._default))
            if not box:
                return ""
            if box.CurrentHasKeyboardFocus:
                return None
            pattern = box.GetCurrentPattern(uia.UIA_ValuePatternId)
            if not pattern:
                return ""
            value = pattern.QueryInterface(uia.IUIAutomationValuePattern).CurrentValue
        except Exception:   # ventana cerrándose, navegador sin árbol de accesibilidad…
            log.debug("No se pudo leer la URL", exc_info=True)
            return ""
        return normalize(value or "")


def normalize(raw: str) -> str:
    """'https://drive.google.com/drive/u/0?x=1#y' → 'drive.google.com/drive/u/0'."""
    raw = raw.strip()
    if not raw or " " in raw:   # texto que se está escribiendo en la barra, no una URL
        return ""
    try:
        parts = urlsplit(raw if _HAS_SCHEME.match(raw) else f"https://{raw}")
        if parts.scheme not in ("http", "https", "file") or not parts.hostname:
            return ""
        host = parts.hostname
        if parts.port:   # «about:blank», «chrome:newtab»… se leen como un puerto no numérico
            host = f"{host}:{parts.port}"
    except ValueError:
        return ""
    return f"{host}{parts.path.rstrip('/')}"[:300]


def domain(url: str) -> str:
    return url.split("/", 1)[0]
