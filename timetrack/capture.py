"""Captura de la ventana activa y detección de cambios visuales."""

from dataclasses import dataclass

import mss
from PIL import Image, ImageChops, ImageStat

from . import win32

# Huella pequeña en escala de grises: suficiente para saber si la pantalla cambió.
_FINGERPRINT_SIZE = (64, 36)


@dataclass
class Capture:
    width: int
    height: int
    bgra: bytes          # píxeles en BGRA8, el formato que espera Windows.Media.Ocr
    fingerprint: Image.Image


class WindowCapturer:
    def __init__(self) -> None:
        self._sct = mss.mss()

    def capture(self, hwnd) -> Capture | None:
        rect = win32.get_window_rect(hwnd)
        if rect is None:
            return None
        left, top, right, bottom = _clip_to_screens(rect, self._sct.monitors[0])
        if right - left < 50 or bottom - top < 50:
            return None
        shot = self._sct.grab({"left": left, "top": top,
                               "width": right - left, "height": bottom - top})
        bgra = bytearray(shot.bgra)
        bgra[3::4] = b"\xff" * (len(bgra) // 4)   # alfa opaco
        fingerprint = (Image.frombytes("RGB", shot.size, shot.rgb)
                       .convert("L").resize(_FINGERPRINT_SIZE, Image.Resampling.BILINEAR))
        return Capture(shot.width, shot.height, bytes(bgra), fingerprint)

    def close(self) -> None:
        self._sct.close()


def visual_change(a: Image.Image | None, b: Image.Image) -> float:
    """Diferencia media (0-255) entre dos huellas; inf si no hay referencia."""
    if a is None or a.size != b.size:
        return float("inf")
    return ImageStat.Stat(ImageChops.difference(a, b)).mean[0]


def _clip_to_screens(rect, virtual):
    left, top, right, bottom = rect
    v_left, v_top = virtual["left"], virtual["top"]
    v_right, v_bottom = v_left + virtual["width"], v_top + virtual["height"]
    return max(left, v_left), max(top, v_top), min(right, v_right), min(bottom, v_bottom)
