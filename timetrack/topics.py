"""«Tema» de la actividad: el asunto del correo abierto, deducido del OCR por tamaño de letra.

Solo se aplica a las aplicaciones de [[topic.apps]] en config.toml, reconocidas por la URL o
por el título de la ventana (son webs: Outlook, Gmail). En cada una se busca, dentro de una zona de la
ventana (el panel de lectura), la línea con la letra más grande; en estos clientes el asunto
del mensaje abierto se pinta más grande que la bandeja, los botones y el cuerpo.
"""

import re
import statistics
from dataclasses import dataclass

from .ocr import OcrLine

_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
_LEADING_SYMBOLS = re.compile(r"^[^\w(\[\"'¿¡#]+", re.UNICODE)
_NOISE = re.compile(r"https?://|www\.|@|\[email\]", re.IGNORECASE)

# La línea elegida debe destacar sobre el resto; si no, no se arriesga un tema.
_MIN_RATIO = 1.12
_MIN_LETTERS = 4


@dataclass(frozen=True)
class TopicApp:
    name: str
    title: re.Pattern | None
    url: re.Pattern | None
    area: tuple[float, float, float, float]   # x0, y0, x1, y1 como fracción de la ventana

    def matches(self, title: str, url: str) -> bool:
        return bool((self.url and url and self.url.search(url))
                    or (self.title and self.title.search(title)))


class TopicExtractor:
    def __init__(self, cfg: dict) -> None:
        self.apps = [_parse_app(a) for a in cfg.get("apps", [])]

    def app_for(self, title: str, url: str = "") -> TopicApp | None:
        return next((a for a in self.apps if a.matches(title, url)), None)

    def extract(self, app: TopicApp, lines: list[OcrLine], width: int, height: int) -> str:
        if not lines:
            return ""
        x0, y0, x1, y1 = (app.area[0] * width, app.area[1] * height,
                          app.area[2] * width, app.area[3] * height)
        candidates = [ln for ln in lines
                      if x0 <= ln.x <= x1 and y0 <= ln.y <= y1
                      and len(_LETTERS.findall(ln.text)) >= _MIN_LETTERS
                      and not _NOISE.search(ln.text)]
        if not candidates:
            return ""
        typical = statistics.median(ln.height for ln in lines)
        best = max(candidates, key=lambda ln: (ln.height, -ln.y))
        if best.height < typical * _MIN_RATIO:
            return ""
        # Un asunto largo puede partirse en dos líneas del mismo tamaño, una debajo de otra.
        parts = [best]
        for ln in sorted(candidates, key=lambda ln: ln.y):
            last = parts[-1]
            if (ln.y > last.y and ln.y - last.y <= last.height * 1.8
                    and abs(ln.height - best.height) <= 1 and abs(ln.x - best.x) <= best.height):
                parts.append(ln)
        text = " ".join(p.text for p in parts)
        return " ".join(_LEADING_SYMBOLS.sub("", text).split())[:200]


def _parse_app(raw: dict) -> TopicApp:
    area = tuple(float(v) for v in raw.get("area", (0.0, 0.0, 1.0, 1.0)))
    if len(area) != 4:
        raise ValueError(f"topic.apps «{raw.get('name')}»: area debe tener 4 valores")
    title, url = raw.get("title"), raw.get("url")
    if not (title or url):
        raise ValueError(f"topic.apps «{raw.get('name')}»: falta title o url")
    return TopicApp(name=str(raw.get("name", "")), title=re.compile(title) if title else None,
                    url=re.compile(url) if url else None, area=area)
