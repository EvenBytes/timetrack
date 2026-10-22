"""Reuniones de hoy, leídas por OCR de la vista de calendario (Outlook o Google Calendar).

Solo se necesitan los títulos de la columna de hoy, no sus horas: cuando el usuario vuelve de
una ausencia larga se le pregunta si estuvo en alguna de ellas, y elige él.

Nada depende del tamaño de la pantalla: la columna de hoy se localiza con las cabeceras de los
días que encuentra el OCR («22 • 22 Thursday», «THU 22») y sus fronteras con las cabeceras
vecinas. La fila de eventos de todo el día («Office») queda por encima de la primera hora del eje
horario, y se descarta.
"""

import difflib
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .ocr import OcrLine

_WEEKDAYS = [
    ("monday", "mon", "lunes", "lun"),
    ("tuesday", "tue", "martes", "mar"),
    ("wednesday", "wed", "miércoles", "miercoles", "mié", "mie"),
    ("thursday", "thu", "jueves", "jue"),
    ("friday", "fri", "viernes", "vie"),
    ("saturday", "sat", "sábado", "sabado", "sáb", "sab"),
    ("sunday", "sun", "domingo", "dom"),
]
_WEEKDAY_OF = {name: i for i, names in enumerate(_WEEKDAYS) for name in names}
# Etiquetas del eje horario: «11», «09:00», «9 AM», «13.00».
_AXIS = re.compile(r"^\d{1,2}([:.]\d{2})?\s*(am|pm|a\.\s?m\.|p\.\s?m\.)?$", re.IGNORECASE)
_TIME = r"\d{1,2}[:.]\d{2}"
_LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)
# Lo que sigue al título en la misma línea: la hora de Google («, 13:00»), el aviso de Teams.
_TAIL = re.compile(rf"(,\s*{_TIME}.*$)|(\s+{_TIME}\s*[-–—]\s*{_TIME}.*$)|"
                   r"(\s*,?\s*(microsoft teams meeting|reunión de microsoft teams).*$)", re.IGNORECASE)
# El borde de color del evento se lee a veces como una letra suelta delante: «I#DIR…», «f Integr…».
_BORDER = re.compile(r"^[Il|f!]\s+(?=\S)|^[Il|](?=[#\[(])")
_MIN_LETTERS = 4


@dataclass(frozen=True)
class CalendarApp:
    name: str
    title: re.Pattern | None
    url: re.Pattern | None
    header_align: str   # "left" (Outlook) o "center" (Google): dónde va el nombre del día

    def matches(self, title: str, url: str) -> bool:
        return bool((self.url and url and self.url.search(url))
                    or (self.title and self.title.search(title)))


class CalendarReader:
    def __init__(self, cfg: dict) -> None:
        self.apps = [_parse_app(a) for a in cfg.get("apps", [])]
        ignore = cfg.get("ignore")
        self.ignore = re.compile(ignore) if ignore else None
        self.ask_after_s = float(cfg.get("ask_after_min", 20)) * 60

    def app_for(self, title: str, url: str = "") -> CalendarApp | None:
        return next((a for a in self.apps if a.matches(title, url)), None)

    def today_events(self, app: CalendarApp, lines: list[OcrLine], today: date) -> list[str]:
        """Títulos de los eventos de hoy visibles en la captura; [] si hoy no está en pantalla."""
        column = _today_column(app, lines, today)
        if not column:
            return []
        left, right, header_y, header_h = column
        top = _grid_top(lines, left) or header_y + header_h * 3
        inside = sorted((ln for ln in lines if left <= ln.x < right and ln.y >= top),
                        key=lambda ln: (ln.y, ln.x))
        titles: list[str] = []
        previous: list[OcrLine] = []
        for ln in inside:
            # Las líneas pegadas debajo de otra (sala, organizador, hora) son del mismo evento.
            continues = any(abs(ln.x - p.x) <= p.height * 1.5
                            and 0 < ln.y - p.y <= max(p.height, ln.height) * 1.6 for p in previous)
            previous.append(ln)
            if continues or re.match(_TIME, ln.text.strip()):
                continue
            title = self.clean(ln.text)
            if title and not any(_similar(title, t) for t in titles):
                titles.append(title)
        return titles

    def clean(self, text: str) -> str:
        title = _BORDER.sub("", " ".join(text.split()))
        title = _TAIL.sub("", title).strip(" ,.-–—·•!")
        if len(_LETTERS.findall(title)) < _MIN_LETTERS:
            return ""
        if self.ignore and self.ignore.search(title):
            return ""
        return title[:120]


def merge_titles(known: list[str], seen: list[str]) -> list[str]:
    """Añade a los títulos guardados los nuevos; si uno nuevo es la versión completa de otro
    guardado (se leyó cortado o con un error de OCR), lo sustituye."""
    result = list(known)
    for title in seen:
        match = next((i for i, k in enumerate(result) if _similar(title, k)), None)
        if match is None:
            result.append(title)
        elif len(title) > len(result[match]):
            result[match] = title
    return result


# --- geometría ----------------------------------------------------------------------------

def _today_column(app: CalendarApp, lines: list[OcrLine],
                  today: date) -> tuple[float, float, float, float] | None:
    """(izquierda, derecha, y de la cabecera, altura) de la columna de hoy, si se ve."""
    headers = [(ln, _WEEKDAY_OF[_word(ln.text)]) for ln in lines if _word(ln.text) in _WEEKDAY_OF]
    if not headers:
        return None
    # La fila de cabeceras: la altura con más nombres de día distintos (descarta un «Monday»
    # suelto en otra parte de la pantalla).
    rows: dict[int, list] = {}
    for ln, wd in headers:
        rows.setdefault(round(ln.y / max(ln.height, 1) / 2), []).append((ln, wd))
    row = max(rows.values(), key=lambda r: len({wd for _, wd in r}))
    row.sort(key=lambda h: h[0].x)
    pos = {wd: (ln.x + ln.width / 2 if app.header_align == "center" else ln.x) for ln, wd in row}
    wd_today = today.weekday()
    if wd_today not in pos:
        return None
    ordered = sorted(pos)
    steps = [(pos[b] - pos[a]) / (b - a) for a, b in zip(ordered, ordered[1:]) if b > a]
    gap = statistics.median(steps) if steps else None
    here = pos[wd_today]
    prev_pos, next_pos = pos.get(wd_today - 1), pos.get(wd_today + 1)
    if app.header_align == "center":
        left = (prev_pos + here) / 2 if prev_pos is not None else (here - gap / 2 if gap else 0)
        right = (here + next_pos) / 2 if next_pos is not None else (here + gap / 2 if gap else 1e9)
    else:
        # Nombre a la izquierda de la columna: la frontera queda un poco antes de cada nombre.
        def edge(p, step):
            return p - step * 0.25
        left = edge(here, (here - prev_pos) if prev_pos is not None else (gap or 0))
        right = edge(next_pos, next_pos - here) if next_pos is not None else (here + gap if gap else 1e9)
    header = next(ln for ln, wd in row if wd == wd_today)
    if not _is_today(lines, row, pos, app, today, header):
        return None
    return left, right, header.y, header.height


def _is_today(lines, row, pos, app, today: date, header: OcrLine) -> bool:
    """La columna de hoy lleva el número de hoy (o sus vecinas el de ayer y mañana): así se
    sabe que la semana en pantalla es la actual y no otra con el mismo jueves."""
    band = [ln for ln in lines if abs(ln.y - header.y) <= header.height * 4]

    def numbers_near(x: float) -> set[int]:
        found = set()
        for ln in band:
            cx = ln.x + ln.width / 2 if app.header_align == "center" else ln.x
            if abs(cx - x) <= max(header.height * 3, 30):
                found.update(int(n) for n in re.findall(r"\b(\d{1,2})\b", ln.text))
        return found

    wd = today.weekday()
    if today.day in numbers_near(pos[wd]):
        return True
    for delta in (-1, 1):
        if wd + delta in pos and (today + timedelta(days=delta)).day in numbers_near(pos[wd + delta]):
            return True
    return False


def _grid_top(lines: list[OcrLine], grid_left: float) -> float | None:
    """Algo por encima de la primera hora del eje horario: lo que queda más arriba es la fila de
    eventos de todo el día. El eje es una columna de horas alineadas a la izquierda de la rejilla."""
    labels = [ln for ln in lines if _AXIS.match(ln.text.strip()) and ln.x < grid_left]
    columns: dict[int, list[OcrLine]] = {}
    for ln in labels:
        columns.setdefault(round(ln.x / 12), []).append(ln)
    axis = [c for c in columns.values() if len(c) >= 3]
    if not axis:
        return None
    col = sorted(max(axis, key=lambda c: (statistics.median(ln.x for ln in c), len(c))),
                 key=lambda ln: ln.y)
    spacing = statistics.median(b.y - a.y for a, b in zip(col, col[1:]))
    return col[0].y - spacing * 0.25


def _word(text: str) -> str:
    return re.sub(r"[^\w]", "", text.strip().lower(), flags=re.UNICODE)


def _norm(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower(), flags=re.UNICODE)


def _similar(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    short, long_ = sorted((na, nb), key=len)
    return long_.startswith(short) or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.85


def _parse_app(raw: dict) -> CalendarApp:
    title, url = raw.get("title"), raw.get("url")
    if not (title or url):
        raise ValueError(f"calendar.apps «{raw.get('name')}»: falta title o url")
    align = raw.get("header_align", "left")
    if align not in ("left", "center"):
        raise ValueError(f"calendar.apps «{raw.get('name')}»: header_align debe ser left o center")
    return CalendarApp(name=str(raw.get("name", "")), title=re.compile(title) if title else None,
                       url=re.compile(url) if url else None, header_align=align)
