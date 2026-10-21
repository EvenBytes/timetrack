"""Modo aplicación: icono en la bandeja del sistema + seguimiento en segundo plano + panel web."""

import logging
import socket
import sys
import threading
import webbrowser
import winreg
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

from .config import Config
from .tracker import Tracker
from .web import Panel

log = logging.getLogger("timetrack")

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = "timetrack"


def run_app(cfg: Config) -> int:
    if _port_in_use(cfg.ui.port):
        # Ya hay una instancia: en lugar de arrancar otra, abre su panel.
        webbrowser.open(f"http://127.0.0.1:{cfg.ui.port}/")
        return 0

    tracker = Tracker(cfg)
    tracker_thread = threading.Thread(target=tracker.run, name="tracker", daemon=True)
    tracker_thread.start()
    panel = Panel(cfg, tracker)
    panel.start_in_thread()
    if cfg.ui.open_browser_on_start:
        webbrowser.open(panel.url)

    def on_open(icon, item):
        webbrowser.open(panel.url)

    def on_toggle_pause(icon, item):
        tracker.resume() if tracker.status().paused else tracker.pause()
        icon.icon = _icon_image(paused=tracker.status().paused)
        icon.title = _tooltip(tracker)

    def on_toggle_autostart(icon, item):
        set_autostart(not autostart_enabled())

    def on_quit(icon, item):
        tracker.stop()
        tracker_thread.join(timeout=5)
        panel.shutdown()
        icon.stop()

    icon = pystray.Icon(
        "timetrack", _icon_image(paused=False), "timetrack",
        menu=pystray.Menu(
            pystray.MenuItem("Abrir panel", on_open, default=True),
            pystray.MenuItem(lambda item: "▶  Reanudar" if tracker.status().paused
                             else "⏸  Pausar", on_toggle_pause),
            pystray.MenuItem("Iniciar con Windows", on_toggle_autostart,
                             checked=lambda item: autostart_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", on_quit),
        ))

    def refresh_tooltip():
        # Actualiza el texto al pasar el ratón por el icono con la actividad en curso.
        while not tracker._stop.wait(10):
            icon.title = _tooltip(tracker)

    threading.Thread(target=refresh_tooltip, name="tooltip", daemon=True).start()
    icon.run()
    return 0


def _tooltip(tracker: Tracker) -> str:
    s = tracker.status()
    if s.error:
        return f"timetrack · error: {s.error}"[:127]
    if s.paused:
        return "timetrack · en pausa"
    return f"timetrack · {s.task or '…'} ({s.process})"[:127]   # límite de Windows: 128


def _icon_image(paused: bool) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    face = (247, 144, 9, 255) if paused else (59, 91, 219, 255)
    d.ellipse((4, 4, size - 4, size - 4), fill=face)
    if paused:
        d.rectangle((22, 18, 29, 46), fill="white")
        d.rectangle((35, 18, 42, 46), fill="white")
    else:   # esfera de reloj
        c = size // 2
        d.line((c, c, c, 14), fill="white", width=5)
        d.line((c, c, 45, c + 6), fill="white", width=5)
        d.ellipse((c - 4, c - 4, c + 4, c + 4), fill="white")
    return img


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


# --- arranque con Windows ----------------------------------------------------------------

def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    # En desarrollo: pythonw (sin consola) ejecutando el paquete desde la carpeta del proyecto.
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    root = Path(__file__).resolve().parent.parent
    return f'cmd /c start "" /d "{root}" "{pythonw}" -m timetrack app'


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            winreg.QueryValueEx(key, _RUN_VALUE)
            return True
    except FileNotFoundError:
        return False


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
