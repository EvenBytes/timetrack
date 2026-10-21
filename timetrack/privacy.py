"""Filtros de privacidad: qué no se captura y qué se enmascara antes de guardar."""

import fnmatch
import re

_REDACTIONS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}\b"), "[iban]"),
    (re.compile(r"\b\d(?:[ -]?\d){12,18}\b"), "[numero]"),
    (re.compile(r"(?i)(contrase(?:ñ|n)a|password|passwd|pwd|token|secret)\s*[:=]\s*\S+"),
     r"\1: [oculto]"),
]


class PrivacyFilter:
    def __init__(self, cfg: dict) -> None:
        self._processes = [p.lower() for p in cfg.get("exclude_processes", [])]
        self._titles = [re.compile(p) for p in cfg.get("exclude_title_regex", [])]
        self._redact = cfg.get("redact", True)
        self._max_chars = cfg.get("max_ocr_chars", 4000)

    def ocr_allowed(self, process: str, title: str) -> bool:
        proc = process.lower()
        if any(fnmatch.fnmatch(proc, p) for p in self._processes):
            return False
        return not any(r.search(title) for r in self._titles)

    def clean(self, text: str) -> str:
        if self._redact:
            for pattern, repl in _REDACTIONS:
                text = pattern.sub(repl, text)
        return text[: self._max_chars]
