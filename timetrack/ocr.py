"""OCR con el motor nativo de Windows (Windows.Media.Ocr): local, sin red y sin instalar modelos."""

import asyncio
from dataclasses import dataclass

from PIL import Image
from winrt.windows.globalization import Language
from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
from winrt.windows.media.ocr import OcrEngine
from winrt.windows.storage.streams import DataWriter

from .capture import Capture


class OcrUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OcrLine:
    text: str
    x: float        # píxeles dentro de la captura
    y: float
    height: float   # altura de la palabra más alta: aproxima el tamaño de letra
    width: float = 0.0


class WindowsOcr:
    def __init__(self, language: str = "") -> None:
        if language:
            lang = Language(language)
            if not OcrEngine.is_language_supported(lang):
                raise OcrUnavailable(
                    f"El idioma OCR '{language}' no está instalado en Windows "
                    "(Configuración > Hora e idioma > Idioma > Opciones > Reconocimiento óptico).")
            self._engine = OcrEngine.try_create_from_language(lang)
        else:
            self._engine = OcrEngine.try_create_from_user_profile_languages()
        if self._engine is None:
            raise OcrUnavailable("No hay ningún idioma OCR disponible en este Windows.")
        self.language = self._engine.recognizer_language.language_tag

    def recognize(self, capture: Capture) -> str:
        return "\n".join(line.text for line in self.recognize_lines(capture))

    def recognize_lines(self, capture: Capture, scale: int = 1,
                        split_gap: float = 0.0) -> list[OcrLine]:
        """Líneas reconocidas con su posición en píxeles de la captura original.

        scale > 1 amplía la imagen antes del OCR: el texto pequeño y gris (los eventos y el eje
        horario de un calendario) solo se reconoce bien así. Cuesta más tiempo.
        split_gap > 0 parte una línea donde el hueco entre dos palabras supera esas veces su
        altura: el OCR junta en una línea dos eventos de calendario que están uno al lado del otro.
        """
        width, height, bgra = capture.width, capture.height, capture.bgra
        if scale > 1:
            # Redimensionar no mezcla canales, así que BGRA se puede tratar como si fuera RGBA.
            img = Image.frombytes("RGBA", (width, height), bgra)
            width, height = width * scale, height * scale
            bgra = img.resize((width, height), Image.LANCZOS).tobytes()
        return asyncio.run(self._recognize(width, height, bgra, scale, split_gap))

    async def _recognize(self, width: int, height: int, bgra: bytes, scale: int,
                         split_gap: float) -> list[OcrLine]:
        max_dim = OcrEngine.max_image_dimension
        if width > max_dim or height > max_dim:
            return []
        writer = DataWriter()
        writer.write_bytes(bgra)
        # winrt 3.x solo expone la sobrecarga sin alfa (premultiplicado); el alfa ya es opaco.
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height)
        result = await self._engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            words = [(w.text, w.bounding_rect) for w in line.words]
            if not words:
                continue
            groups = [[words[0]]]
            for word in words[1:]:
                prev = groups[-1][-1][1]
                gap = word[1].x - (prev.x + prev.width)
                if split_gap and gap > split_gap * max(prev.height, word[1].height):
                    groups.append([word])
                else:
                    groups[-1].append(word)
            for group in groups:
                rects = [r for _, r in group]
                text = line.text if len(groups) == 1 else " ".join(t for t, _ in group)
                left = min(r.x for r in rects)
                lines.append(OcrLine(text, left / scale, min(r.y for r in rects) / scale,
                                     max(r.height for r in rects) / scale,
                                     (max(r.x + r.width for r in rects) - left) / scale))
        return lines


def available_languages() -> list[str]:
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
