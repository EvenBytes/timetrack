"""Persistencia en SQLite. Se guardan datos en bruto; la clasificación se calcula al leer,
así un cambio en las reglas se aplica también al histórico."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .titles import clean_title

AWAY = "(ausente)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    id        INTEGER PRIMARY KEY,
    start_ts  REAL NOT NULL,
    end_ts    REAL NOT NULL,
    process   TEXT NOT NULL,
    exe_path  TEXT NOT NULL DEFAULT '',
    title     TEXT NOT NULL DEFAULT '',
    topic     TEXT NOT NULL DEFAULT '',
    url       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_activities_start ON activities(start_ts);

CREATE TABLE IF NOT EXISTS ocr_samples (
    id          INTEGER PRIMARY KEY,
    activity_id INTEGER NOT NULL REFERENCES activities(id) ON DELETE CASCADE,
    ts          REAL NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ocr_activity ON ocr_samples(activity_id);
"""


@dataclass
class Activity:
    id: int
    start_ts: float
    end_ts: float
    process: str
    exe_path: str
    title: str
    ocr_text: str = ""
    topic: str = ""
    url: str = ""   # dominio + ruta en los navegadores
    category: str = ""   # tipo de aplicación; no se guarda, lo calcula report.classified

    @property
    def app(self) -> str:
        """Nombre a mostrar: el dominio en los navegadores, el ejecutable en el resto."""
        return self.url.split("/", 1)[0] or self.process

    @property
    def duration(self) -> float:
        return max(0.0, self.end_ts - self.start_ts)

    @property
    def is_away(self) -> bool:
        return self.process == AWAY


class Store:
    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # El tracker escribe y el panel web lee desde otros hilos, cada uno con su conexión.
        self._db = sqlite3.connect(path, timeout=5)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(_SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(activities)")}
        for column in ("topic", "url"):   # bases de datos de versiones anteriores
            if column not in columns:
                self._db.execute(
                    f"ALTER TABLE activities ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        self._db.commit()

    def start_activity(self, ts: float, process: str, exe_path: str, title: str,
                       topic: str = "", url: str = "") -> int:
        cur = self._db.execute(
            "INSERT INTO activities(start_ts, end_ts, process, exe_path, title, topic, url) "
            "VALUES (?,?,?,?,?,?,?)", (ts, ts, process, exe_path, title, topic, url))
        self._db.commit()
        return cur.lastrowid

    def extend_activity(self, activity_id: int, ts: float) -> None:
        self._db.execute("UPDATE activities SET end_ts=? WHERE id=?", (ts, activity_id))
        self._db.commit()

    def set_topic(self, activity_id: int, topic: str) -> None:
        self._db.execute("UPDATE activities SET topic=? WHERE id=?", (topic, activity_id))
        self._db.commit()

    def add_ocr(self, activity_id: int, ts: float, text: str) -> None:
        self._db.execute("INSERT INTO ocr_samples(activity_id, ts, text) VALUES (?,?,?)",
                         (activity_id, ts, text))
        self._db.commit()

    def activities_between(self, start_ts: float, end_ts: float) -> list[Activity]:
        rows = self._db.execute(
            """SELECT a.id, a.start_ts, a.end_ts, a.process, a.exe_path, a.title,
                      COALESCE(group_concat(o.text, char(10)), ''), a.topic, a.url
               FROM activities a LEFT JOIN ocr_samples o ON o.activity_id = a.id
               WHERE a.end_ts > ? AND a.start_ts < ?
               GROUP BY a.id ORDER BY a.start_ts""",
            (start_ts, end_ts)).fetchall()
        acts = [Activity(*row) for row in rows]
        for act in acts:   # recorta a la ventana pedida (p. ej. actividades que cruzan medianoche)
            act.start_ts = max(act.start_ts, start_ts)
            act.end_ts = min(act.end_ts, end_ts)
            act.title = clean_title(act.title)   # el histórico anterior a la limpieza también
        return acts

    def ocr_samples(self, activity_id: int) -> list[tuple[float, str]]:
        return self._db.execute(
            "SELECT ts, text FROM ocr_samples WHERE activity_id=? ORDER BY ts",
            (activity_id,)).fetchall()

    def close(self) -> None:
        self._db.close()
