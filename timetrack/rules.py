"""Clasificación de actividades en tareas mediante reglas.

Cada regla puede tener los campos:
  process  lista de nombres de ejecutable (admite comodines: "*.exe", "Code*.exe")
  title    lista de regex sobre el título de la ventana
  topic    lista de regex sobre el tema (asunto del correo abierto)
  url      lista de regex sobre la dirección en los navegadores (dominio + ruta, sin https://)
  ocr      lista de regex sobre el texto reconocido por OCR
  text     lista de regex sobre título + tema + OCR juntos
Dentro de un campo basta con que coincida un patrón; entre campos deben cumplirse todos.
Gana la primera regla del fichero que coincida.
"""

import fnmatch
import re
from dataclasses import dataclass, field

from .store import Activity

UNCLASSIFIED = "Sin clasificar"
OTHER_CATEGORY = "Otros"   # tipo de aplicación cuando no coincide ninguna regla
AWAY_TASK = "Ausente"


@dataclass
class Rule:
    task: str
    process: list[str] = field(default_factory=list)
    title: list[re.Pattern] = field(default_factory=list)
    topic: list[re.Pattern] = field(default_factory=list)
    url: list[re.Pattern] = field(default_factory=list)
    ocr: list[re.Pattern] = field(default_factory=list)
    text: list[re.Pattern] = field(default_factory=list)

    def matches(self, act: Activity) -> bool:
        proc = act.process.lower()
        if self.process and not any(fnmatch.fnmatch(proc, p) for p in self.process):
            return False
        if self.title and not any(r.search(act.title) for r in self.title):
            return False
        if self.topic and not any(r.search(act.topic) for r in self.topic):
            return False
        if self.url and not any(r.search(act.url) for r in self.url):
            return False
        if self.ocr and not any(r.search(act.ocr_text) for r in self.ocr):
            return False
        combined = f"{act.title}\n{act.topic}\n{act.ocr_text}"
        if self.text and not any(r.search(combined) for r in self.text):
            return False
        return True


class RuleSet:
    def __init__(self, raw_rules: list[dict], fallback: str = UNCLASSIFIED) -> None:
        self.rules = [self._parse(i, r) for i, r in enumerate(raw_rules)]
        self.fallback = fallback

    @staticmethod
    def _parse(index: int, raw: dict) -> Rule:
        task = str(raw.get("task") or "").strip()
        if not task:
            raise ValueError(f"La regla #{index + 1} no tiene nombre de tarea")

        def compile_all(key: str) -> list[re.Pattern]:
            patterns = []
            for p in _as_list(raw.get(key)):
                try:
                    patterns.append(re.compile(p))
                except re.error as exc:
                    raise ValueError(f"Regla '{task}', campo {key}: regex no válida "
                                     f"«{p}» ({exc})") from None
            return patterns

        rule = Rule(
            task=task,
            process=[p.lower() for p in _as_list(raw.get("process"))],
            title=compile_all("title"),
            topic=compile_all("topic"),
            url=compile_all("url"),
            ocr=compile_all("ocr"),
            text=compile_all("text"),
        )
        if not (rule.process or rule.title or rule.topic or rule.url or rule.ocr or rule.text):
            raise ValueError(f"La regla '{rule.task}' no tiene ninguna condición")
        return rule

    def classify(self, act: Activity) -> str:
        if act.is_away:
            return AWAY_TASK
        for rule in self.rules:
            if rule.matches(act):
                return rule.task
        return self.fallback


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)
