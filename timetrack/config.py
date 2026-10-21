import json
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Empaquetado como .exe, la configuración y los datos viven junto al ejecutable.
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

RULE_FIELDS = ("process", "title", "topic", "url", "ocr", "text")

_RULES_HEADER = """\
# Reglas de clasificación. Gana la PRIMERA que coincida, así que pon las específicas arriba.
# Se pueden editar aquí o desde el panel (http://127.0.0.1:<puerto>, pestaña Reglas).
#
#   task     nombre de la tarea (obligatorio)
#   process  ejecutables, admite comodines           "Code.exe", "*teams*.exe"
#   title    regex sobre el título de la ventana
#   topic    regex sobre el tema (asunto del correo abierto en Outlook/Gmail)
#   url      regex sobre la dirección en los navegadores, sin https://  "drive\\.google\\.com"
#   ocr      regex sobre el texto reconocido en pantalla
#   text     regex sobre título + tema + OCR
#
# Dentro de un campo basta con que coincida un patrón; entre campos se exigen todos.
# Usa (?i) para no distinguir mayúsculas.
"""


@dataclass
class Settings:
    poll_interval_s: float = 1.0
    ocr_interval_s: float = 15.0
    ocr_after_switch_s: float = 2.0
    change_threshold: float = 3.0
    idle_threshold_s: float = 300.0
    media_keeps_present: bool = True   # micrófono o cámara en uso = presente (llamadas)
    ocr_language: str = ""
    db_path: str = "data/timesheet.db"


@dataclass
class UiSettings:
    port: int = 8765
    open_browser_on_start: bool = False


@dataclass
class Config:
    settings: Settings
    privacy: dict
    rules: list[dict]
    ui: UiSettings = field(default_factory=UiSettings)
    topic: dict = field(default_factory=dict)
    rules_path: Path = ROOT / "rules.toml"
    # Tipos de aplicación (Correo, Chats, Documentos…): clasificación paralela a las tareas.
    categories_path: Path = ROOT / "categories.toml"

    def rules_file(self, kind: str) -> Path:
        if kind not in ("tasks", "categories"):
            raise ValueError(f"Tipo de reglas desconocido: {kind}")
        return self.categories_path if kind == "categories" else self.rules_path

    @property
    def db_path(self) -> Path:
        path = Path(self.settings.db_path)
        return path if path.is_absolute() else ROOT / path


def load_config(config_path: Path | None = None, rules_path: Path | None = None) -> Config:
    config_path = config_path or ROOT / "config.toml"
    rules_path = rules_path or ROOT / "rules.toml"
    raw = tomllib.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    return Config(
        settings=Settings(**raw.get("settings", {})),
        privacy=raw.get("privacy", {}),
        rules=load_rules(rules_path),
        ui=UiSettings(**raw.get("ui", {})),
        topic=raw.get("topic", {}),
        rules_path=rules_path,
        categories_path=rules_path.parent / "categories.toml",
    )


def load_rules(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return tomllib.loads(path.read_text(encoding="utf-8")).get("rules", [])


def save_rules(path: Path, rules: list[dict]) -> None:
    """Escribe el fichero de reglas conservando su comentario de cabecera (o el de serie)."""
    out = [_header_of(path) or _RULES_HEADER]
    for rule in rules:
        out.append("[[rules]]")
        out.append(f"task = {_toml_str(rule['task'])}")
        for key in RULE_FIELDS:
            values = rule.get(key) or []
            if values:
                out.append(f"{key} = [{', '.join(_toml_str(v) for v in values)}]")
        out.append("")
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out), encoding="utf-8")
    tmp.replace(path)   # escritura atómica: nunca queda un rules.toml a medias


def _toml_str(value: str) -> str:
    # Una cadena básica TOML admite los mismos escapes que JSON para lo que usamos aquí.
    return json.dumps(value, ensure_ascii=False)


def _header_of(path: Path) -> str:
    if not path.exists():
        return ""
    header = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            break
        header.append(line)
    return "\n".join(header).strip() + "\n" if any(header) else ""
