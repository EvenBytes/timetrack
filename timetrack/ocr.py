"""OCR con el motor nativo de Windows (Windows.Media.Ocr): local, sin red y sin instalar modelos."""

import asyncio
from dataclasses import dataclass

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

    def recognize_lines(self, capture: Capture) -> list[OcrLine]:
        return asyncio.run(self._recognize(capture))

    async def _recognize(self, capture: Capture) -> list[OcrLine]:
        max_dim = OcrEngine.max_image_dimension
        if capture.width > max_dim or capture.height > max_dim:
            return []
        writer = DataWriter()
        writer.write_bytes(capture.bgra)
        # winrt 3.x solo expone la sobrecarga sin alfa (premultiplicado); el alfa ya es opaco.
        bitmap = SoftwareBitmap.create_copy_from_buffer(
            writer.detach_buffer(), BitmapPixelFormat.BGRA8, capture.width, capture.height)
        result = await self._engine.recognize_async(bitmap)
        lines = []
        for line in result.lines:
            rects = [word.bounding_rect for word in line.words]
            if rects:
                lines.append(OcrLine(line.text, min(r.x for r in rects), min(r.y for r in rects),
                                     max(r.height for r in rects)))
        return lines


def available_languages() -> list[str]:
    return [lang.language_tag for lang in OcrEngine.available_recognizer_languages]
