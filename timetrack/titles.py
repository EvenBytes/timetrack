"""Limpieza de títulos de ventana.

Los navegadores y otras apps añaden al título datos que no identifican la tarea y que, si se
guardan tal cual, parten una misma actividad en muchas (un contador de notificaciones que
cambia, el icono animado de una terminal…). Se aplica al registrar y al leer el histórico.
"""

import re

_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

# Sufijos de navegador. Edge añade también "y N páginas más" (o "and N more pages")
# y el perfil: "… y 4 páginas más - Perfil 1: Microsoft Edge".
_BROWSER_SUFFIX = re.compile(
    r"(?:\s+(?:y\s+\d+\s+páginas?\s+más|and\s+\d+\s+more\s+pages?))?"
    r"(?:\s+[-—–]\s+(?:Perfil|Profile)\s+[^:]{1,40}:)?"
    r"\s*[-—–]?\s*(?:Microsoft\s*Edge|Google\s*Chrome|Mozilla\s*Firefox|Brave|Opera|Vivaldi)\s*$",
    re.IGNORECASE)

# "(45) Chat | …" o "[3] Bandeja": contadores de no leídos al principio.
_COUNTER_PREFIX = re.compile(r"^\s*[(\[]\d{1,5}\+?[)\]]\s*")

# Iconos y spinners al principio ("◐ timetrack", "✳ tarea", "● fichero.py"): todo lo que no
# sea letra, número o signo de apertura habitual.
_SYMBOL_PREFIX = re.compile(r"^[^\w(\[\"'¿¡#@$€.]+", re.UNICODE)


def clean_title(title: str) -> str:
    title = _INVISIBLE.sub("", title or "")
    title = _BROWSER_SUFFIX.sub("", title)
    title = _COUNTER_PREFIX.sub("", title)
    title = _SYMBOL_PREFIX.sub("", title)
    return " ".join(title.split())
