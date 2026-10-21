"""«Tema» de la actividad: el asunto del correo abierto.

Solo se aplica a las aplicaciones de [[topic.apps]] en config.toml, reconocidas por la URL o
por el título de la ventana (son webs: Outlook, Gmail). Hay dos formas de sacarlo:

- title_topic: una regex con el grupo (?P<topic>…) sobre el título de la ventana. Gmail pone el
  asunto en el título («asunto - email - Org Mail»), así que es la vía fiable; title_skip
  descarta lo que no es un asunto (bandejas y carpetas: «Inbox», «Recibidos»…).
- Si no hay title_topic, por OCR: la línea con la letra más grande, porque el asunto del mensaje
  abierto se pinta más grande que la bandeja, los botones y el cuerpo. Con anchor (Outlook), se
  busca justo encima de la línea de los botones «Reply / Reply all» del panel de lectura, que
  siempre va debajo del asunto; si no hay botones en pantalla, no hay correo abierto. Sin anchor,
  dentro de la zona area de la ventana.
"""

import re
import statistics
from dataclasses import dataclass

from .ocr import OcrLine

_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
_LEADING_SYMBOLS = re.compile(r"^[^\w(\[\"'¿¡#]+", re.UNICODE)
_NOISE = re.compile(r"https?://|www\.|@|\[email\]", re.IGNORECASE)
# La flecha de una etiqueta o un desplegable junto al asunto se lee como una «v» suelta.
_TRAILING_ARROW = re.compile(r"\s+[vV˅⌄∨]$")

# La línea elegida debe destacar sobre el resto; si no, no se arriesga un tema.
_MIN_RATIO = 1.12
_MIN_LETTERS = 4


@dataclass(frozen=True)
class TopicApp:
    name: str
    title: re.Pattern | None
    url: re.Pattern | None
    area: tuple[float, float, float, float]   # x0, y0, x1, y1 como fracción de la ventana
    title_topic: re.Pattern | None = None       # tema desde el título, grupo «topic»
    title_skip: re.Pattern | None = None        # temas del título que no son un asunto
    no_topic: re.Pattern | None = None          # títulos de vistas sin correo abierto (calendario…)
    anchor: re.Pattern | None = None            # línea que va justo debajo del asunto («Reply all»)
    strip: re.Pattern | None = None             # texto de botones pegado al asunto, a quitar

    def matches(self, title: str, url: str) -> bool:
        return bool((self.url and url and self.url.search(url))
                    or (self.title and self.title.search(title)))

    def topic_from_title(self, title: str) -> str:
        m = self.title_topic.search(title) if self.title_topic else None
        topic = " ".join((m.group("topic") if m else "").split())
        if not topic or (self.title_skip and self.title_skip.search(topic)):
            return ""
        return topic[:200]


class TopicExtractor:
    def __init__(self, cfg: dict) -> None:
        self.apps = [_parse_app(a) for a in cfg.get("apps", [])]

    def app_for(self, title: str, url: str = "") -> TopicApp | None:
        return next((a for a in self.apps if a.matches(title, url)), None)

    def extract(self, app: TopicApp, lines: list[OcrLine], width: int, height: int,
                title: str = "") -> str:
        if not lines or (app.no_topic and app.no_topic.search(title)):
            return ""
        typical = statistics.median(ln.height for ln in lines)
        if app.anchor:
            found = self._above_anchor(app, lines, typical)
            if not found:
                return ""
            best, candidates = found
        else:
            x0, y0, x1, y1 = (app.area[0] * width, app.area[1] * height,
                              app.area[2] * width, app.area[3] * height)
            candidates = [ln for ln in lines if x0 <= ln.x <= x1 and y0 <= ln.y <= y1
                          and _is_text(ln)]
            if not candidates:
                return ""
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
        text = " ".join(_LEADING_SYMBOLS.sub("", " ".join(p.text for p in parts)).split())
        if app.strip:
            text = app.strip.sub("", text).strip()
        return _TRAILING_ARROW.sub("", text)[:200]

    @staticmethod
    def _above_anchor(app: TopicApp, lines: list[OcrLine],
                      typical: float) -> tuple[OcrLine, list[OcrLine]] | None:
        """El asunto: la línea más grande justo encima de los botones de respuesta.

        Puede haber varias líneas con «Reply all» (la cinta de opciones, cada mensaje de una
        conversación); se prueban de arriba abajo y vale la primera que tenga encima una línea
        claramente más grande que el texto normal: la de la cinta solo tiene botones encima.
        """
        for anchor in sorted((ln for ln in lines if app.anchor.search(ln.text)), key=lambda ln: ln.y):
            reach = anchor.y - max(anchor.height, typical) * 8   # unas pocas líneas por encima
            candidates = [ln for ln in lines if reach <= ln.y < anchor.y - anchor.height * 0.5
                          and _is_text(ln) and not app.anchor.search(ln.text)]
            if not candidates:
                continue
            best = max(candidates, key=lambda ln: (ln.height, ln.y))
            if best.height >= typical * _MIN_RATIO:
                return best, candidates
        return None


def _is_text(ln: OcrLine) -> bool:
    return len(_LETTERS.findall(ln.text)) >= _MIN_LETTERS and not _NOISE.search(ln.text)


def _parse_app(raw: dict) -> TopicApp:
    area = tuple(float(v) for v in raw.get("area", (0.0, 0.0, 1.0, 1.0)))
    if len(area) != 4:
        raise ValueError(f"topic.apps «{raw.get('name')}»: area debe tener 4 valores")
    title, url = raw.get("title"), raw.get("url")
    if not (title or url):
        raise ValueError(f"topic.apps «{raw.get('name')}»: falta title o url")
    title_topic, title_skip = raw.get("title_topic"), raw.get("title_skip")
    no_topic, strip, anchor = raw.get("no_topic"), raw.get("strip"), raw.get("anchor")
    if title_topic and "(?P<topic>" not in title_topic:
        raise ValueError(f"topic.apps «{raw.get('name')}»: title_topic necesita (?P<topic>…)")
    return TopicApp(name=str(raw.get("name", "")), title=re.compile(title) if title else None,
                    url=re.compile(url) if url else None, area=area,
                    title_topic=re.compile(title_topic) if title_topic else None,
                    title_skip=re.compile(title_skip) if title_skip else None,
                    no_topic=re.compile(no_topic) if no_topic else None,
                    strip=re.compile(strip) if strip else None,
                    anchor=re.compile(anchor) if anchor else None)
