"""Clientes y proyectos imputables.

Un proyecto es una tarea de las reglas (mismo nombre) con un cliente asignado y la marca de si
es imputable. Viven en projects.toml, junto a rules.toml:

    [[clients]]
    name = "Cliente A"

    [[projects]]
    name = "Portal de proveedores"
    client = "Cliente A"
    billable = true
    keywords = ["PORTAL", "proveedores"]

Al crear un proyecto se genera su regla más evidente: el nombre y las palabras clave en el
título de la ventana.
"""

import json
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

NO_CLIENT = "Sin cliente"

_HEADER = """# Clientes y proyectos. Cada proyecto es una tarea de rules.toml (mismo nombre) con su cliente.
# Se editan desde el panel (pestaña Proyectos) o a mano.
#
#   name      nombre del proyecto = nombre de la tarea en las reglas
#   client    cliente al que se imputa
#   billable  true si es imputable
#   keywords  palabras que identifican el proyecto (además del nombre)
"""


@dataclass
class Project:
    name: str
    client: str
    billable: bool = True
    keywords: list[str] = field(default_factory=list)


@dataclass
class Projects:
    clients: list[str] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)

    def get(self, name: str) -> Project | None:
        return next((p for p in self.projects if p.name == name), None)

    def client_of(self, task: str) -> str:
        p = self.get(task)
        return p.client if p else ""

    def billable(self, task: str) -> bool:
        p = self.get(task)
        return bool(p and p.billable)

    def all_clients(self) -> list[str]:
        names = list(self.clients)
        for p in self.projects:
            if p.client not in names:
                names.append(p.client)
        return names


def load_projects(path: Path) -> Projects:
    if not path.exists():
        return Projects()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return Projects(
        clients=[str(c["name"]) for c in raw.get("clients", []) if c.get("name")],
        projects=[Project(name=str(p["name"]), client=str(p.get("client") or ""),
                          billable=bool(p.get("billable", True)),
                          keywords=[str(k) for k in p.get("keywords", [])])
                  for p in raw.get("projects", []) if p.get("name")])


def save_projects(path: Path, data: Projects) -> None:
    q = lambda v: json.dumps(v, ensure_ascii=False)   # cadena básica TOML
    out = [_HEADER]
    for c in data.all_clients():
        out += ["[[clients]]", f"name = {q(c)}", ""]
    for p in data.projects:
        out += ["[[projects]]", f"name = {q(p.name)}", f"client = {q(p.client)}",
                f"billable = {'true' if p.billable else 'false'}"]
        if p.keywords:
            out.append(f"keywords = [{', '.join(q(k) for k in p.keywords)}]")
        out.append("")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    tmp.replace(path)


def validate(data: Projects) -> None:
    seen = set()
    for p in data.projects:
        if not p.name.strip():
            raise ValueError("Hay un proyecto sin nombre")
        if not p.client.strip():
            raise ValueError(f"El proyecto «{p.name}» no tiene cliente")
        if p.name.lower() in seen:
            raise ValueError(f"El proyecto «{p.name}» está repetido")
        seen.add(p.name.lower())


# --- reglas automáticas --------------------------------------------------------------------

def term_pattern(term: str) -> str:
    """«Portal de Proveedores» → «(?i)\\bPortal\\s*de\\s*Proveedores\\b»: sin distinguir
    mayúsculas, con o sin espacios entre palabras y sin coincidir dentro de otra palabra."""
    words = [re.escape(w) for w in term.split()]
    body = r"\s*".join(words)
    # \b solo tiene sentido junto a letras o números («I+D» empieza por letra, «.NET» no).
    start = r"\b" if re.match(r"\w", term.strip()) else ""
    end = r"\b" if re.search(r"\w$", term.strip()) else ""
    return f"(?i){start}{body}{end}"


def generated_rule(project: Project) -> dict:
    """Regla de un proyecto nuevo: su nombre o sus palabras clave en el título de la ventana."""
    terms = [project.name] + [k for k in project.keywords if k.strip()]
    return {"task": project.name, "title": list(dict.fromkeys(term_pattern(t) for t in terms))}


def sync_rule(rules: list[dict], old: Project, new: Project) -> list[dict]:
    """Al cambiar el nombre o las palabras clave de un proyecto, actualiza su regla de título.

    Se sustituyen los patrones que salían del nombre y las palabras clave anteriores y se
    conservan los que se hayan añadido a mano en esa regla. Si no tiene regla de título, se crea.
    """
    rules = [dict(r) for r in rules]
    stale = set(generated_rule(old)["title"])
    fresh = generated_rule(new)["title"]
    for r in rules:
        if r["task"] == new.name and set(r) == {"task", "title"}:
            manual = [p for p in r["title"] if p not in stale and p not in fresh]
            r["title"] = fresh + manual
            return rules
    return insert_rule(rules, generated_rule(new))


def insert_rule(rules: list[dict], rule: dict) -> list[dict]:
    """Pone la regla de un proyecto arriba (lo específico gana a lo genérico), pero detrás de
    las reglas por URL o proceso del principio (p. ej. «WhatsApp = Personal»): un chat personal
    que nombre el proyecto debe seguir siendo personal."""
    rules = list(rules)
    i = 0
    while i < len(rules) and set(rules[i]) - {"task"} <= {"url", "process"}:
        i += 1
    rules.insert(i, rule)
    return rules
