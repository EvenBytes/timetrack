"""Panel web local: informe, línea temporal, pendientes y editor de reglas y de tipos.

Solo escucha en 127.0.0.1. Para que otra web abierta en el navegador no pueda usar la API,
se exige la cabecera Host local (evita DNS rebinding) y JSON en las escrituras (una petición
JSON entre orígenes requiere preflight CORS, que este servidor no concede).
"""

import io
import json
import logging
import threading
from dataclasses import asdict
from datetime import date, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import report
from .config import RULE_FIELDS, Config, load_rules, save_rules
from .rules import OTHER_CATEGORY, RuleSet, UNCLASSIFIED
from .store import Store

log = logging.getLogger("timetrack.web")

_STATIC = Path(__file__).resolve().parent / "static"


class Panel:
    def __init__(self, cfg: Config, tracker=None) -> None:
        self.cfg = cfg
        self.tracker = tracker
        self.port = cfg.ui.port
        self._rules_lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def serve_forever(self) -> None:
        panel = self

        class Handler(_Handler):
            ctx = panel

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        log.info("Panel en %s", self.url)
        self._server.serve_forever()

    def start_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="panel", daemon=True)
        thread.start()
        return thread

    def shutdown(self) -> None:
        if self._server:
            self._server.shutdown()

    # --- datos ---------------------------------------------------------------------------

    def rules(self, kind: str = "tasks") -> RuleSet:
        fallback = OTHER_CATEGORY if kind == "categories" else UNCLASSIFIED
        return RuleSet(load_rules(self.cfg.rules_file(kind)), fallback)

    def items(self, start: date, end: date | None = None) -> report.Classified:
        store = Store(self.cfg.db_path)
        try:
            return report.classified(store, self.rules(), start, end,
                                     categories=self.rules("categories"))
        finally:
            store.close()

    def save_rules(self, raw_rules: list[dict], kind: str = "tasks") -> list[dict]:
        cleaned = [_clean_rule(r) for r in raw_rules]
        RuleSet(cleaned)   # valida: lanza ValueError con un mensaje legible
        with self._rules_lock:
            save_rules(self.cfg.rules_file(kind), cleaned)
        if self.tracker and kind == "tasks":
            self.tracker.reload_rules(cleaned)
        return cleaned


class _Handler(BaseHTTPRequestHandler):
    ctx: Panel
    server_version = "timetrack"

    # --- infraestructura -----------------------------------------------------------------

    def log_message(self, fmt, *args):   # silencia el log por petición de http.server
        log.debug(fmt, *args)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{self.ctx.port}", f"localhost:{self.ctx.port}"}

    def _send(self, status: int, body: bytes, content_type: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200):
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str):
        self._json({"error": message}, status)

    def _body(self) -> dict:
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise PermissionError("Se esperaba application/json")
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def _dispatch(self, method: str):
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "Host no permitido")
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        route = getattr(self, f"{method}_{url.path.strip('/').replace('/', '_').replace('.', '_') or 'index'}", None)
        if route is None:
            return self._error(HTTPStatus.NOT_FOUND, "No encontrado")
        try:
            route(q)
        except (ValueError, KeyError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except PermissionError as exc:
            self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, str(exc))
        except Exception:
            log.exception("Error en %s %s", method, self.path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Error interno (ver log)")

    def do_GET(self):
        self._dispatch("get")

    def do_POST(self):
        self._dispatch("post")

    def do_PUT(self):
        self._dispatch("put")

    # --- rutas ---------------------------------------------------------------------------

    def get_index(self, q):
        body = (_STATIC / "index.html").read_bytes()
        self._send(200, body, "text/html; charset=utf-8",
                   {"Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                               "script-src 'self' 'unsafe-inline'"})

    def get_api_status(self, q):
        t = self.ctx.tracker
        self._json(asdict(t.status()) if t else {"running": False, "paused": False})

    def post_api_pause(self, q):
        self._body()
        if self.ctx.tracker:
            self.ctx.tracker.pause()
        self.get_api_status(q)

    def post_api_resume(self, q):
        self._body()
        if self.ctx.tracker:
            self.ctx.tracker.resume()
        self.get_api_status(q)

    def get_api_day(self, q):
        day = _date(q)
        items = self.ctx.items(day)
        self._json({
            "date": day.isoformat(),
            "worked": report.worked_seconds(items),
            "totals": [_total(t) for t in report.task_totals(items)],
            "categories": [{"category": c.category, "seconds": c.seconds, "tasks": _ranked(c.tasks),
                            "apps": _ranked(c.apps)} for c in report.category_totals(items)],
            "blocks": [_block(b) for b in report.timeline(items)],
            "category_blocks": [_block(b) for b in report.timeline(items, by="category")],
            "activities": [_activity(a, task) for a, task in items],
        })

    def get_api_week(self, q):
        day = _date(q)
        monday = day - timedelta(days=day.weekday())
        days = [monday + timedelta(days=i) for i in range(7)]
        by_category = q.get("by") == "category"
        per_task: dict[str, list[float]] = {}
        for i, d in enumerate(days):
            items = self.ctx.items(d)
            rows = ([(c.category, c.seconds) for c in report.category_totals(items)] if by_category
                    else [(t.task, t.seconds) for t in report.task_totals(items)])
            for name, seconds in rows:
                per_task.setdefault(name, [0.0] * 7)[i] += seconds
        tasks = sorted(({"task": k, "days": v, "total": sum(v)} for k, v in per_task.items()),
                       key=lambda r: -r["total"])
        self._json({"days": [d.isoformat() for d in days], "tasks": tasks,
                    "totals": [sum(t["days"][i] for t in tasks) for i in range(7)]})

    def get_api_activity(self, q):
        activity_id = int(q["id"])
        store = Store(self.ctx.cfg.db_path)
        try:
            samples = store.ocr_samples(activity_id)
        finally:
            store.close()
        self._json({"id": activity_id, "samples": [{"ts": ts, "text": text} for ts, text in samples]})

    def get_api_unclassified(self, q):
        day = _date(q)
        days = max(1, min(31, int(q.get("days", 1))))
        items = self.ctx.items(day - timedelta(days=days - 1), day)
        self._json({"groups": [{"process": p, "title": t, "topic": tp, "seconds": s}
                               for p, t, tp, s in report.unclassified_groups(items)]})

    def get_api_rules(self, q):
        path = self.ctx.cfg.rules_file(q.get("kind", "tasks"))
        self._json({"rules": load_rules(path), "path": str(path)})

    def put_api_rules(self, q):
        body = self._body()
        saved = self.ctx.save_rules(body.get("rules", []), body.get("kind", "tasks"))
        self._json({"rules": saved})

    def post_api_rules_preview(self, q):
        """Qué capturaría una regla (sola, sin importar su posición) en los últimos N días."""
        body = self._body()
        rule_set = RuleSet([_clean_rule(body.get("rule", {}))])
        day = _date(body)
        days = max(1, min(31, int(body.get("days", 7))))
        items = self.ctx.items(day - timedelta(days=days - 1), day)
        rule = rule_set.rules[0]
        by_category = body.get("kind") == "categories"
        hits: dict[tuple[str, str, str, str], float] = {}
        for act, current in items:
            if by_category:
                current = act.category
            if not act.is_away and rule.matches(act):
                key = (act.app, act.title, act.topic, current)
                hits[key] = hits.get(key, 0.0) + act.duration
        ranked = sorted(hits.items(), key=lambda kv: -kv[1])
        self._json({
            "seconds": sum(hits.values()),
            "examples": [{"process": p, "title": t, "topic": tp, "current_task": c, "seconds": s}
                         for (p, t, tp, c), s in ranked[:25]],
        })

    def get_api_export_csv(self, q):
        start = _date(q, "from")
        end = _date(q, "to") if "to" in q else start
        buf = io.StringIO()
        report.export_csv(self.ctx.items(start, end), buf)
        name = f"timetrack_{start.isoformat()}_{end.isoformat()}.csv"
        self._send(200, ("﻿" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                   {"Content-Disposition": f'attachment; filename="{name}"'})


# --- utilidades --------------------------------------------------------------------------

def _date(q: dict, key: str = "date") -> date:
    value = q.get(key)
    return date.fromisoformat(value) if value else date.today()


def _ranked(apps: dict[str, float]) -> list[list]:
    return [[p, s] for p, s in sorted(apps.items(), key=lambda kv: -kv[1])]


def _total(t: report.TaskTotal) -> dict:
    return {"task": t.task, "seconds": t.seconds, "apps": _ranked(t.apps),
            "categories": _ranked(t.categories)}


def _block(b: report.Block) -> dict:
    return {"task": b.task, "start": b.start_ts, "end": b.end_ts, "apps": _ranked(b.apps)}


def _activity(a, task: str) -> dict:
    return {"id": a.id, "start": a.start_ts, "end": a.end_ts, "process": a.process,
            "app": a.app, "url": a.url, "title": a.title, "topic": a.topic, "task": task, "category": a.category, "has_ocr": bool(a.ocr_text),
            "unclassified": task == UNCLASSIFIED}


def _clean_rule(raw: dict) -> dict:
    rule = {"task": str(raw.get("task") or "").strip()}
    for key in RULE_FIELDS:
        values = raw.get(key) or []
        if isinstance(values, str):
            values = [values]
        values = [str(v).strip() for v in values if str(v).strip()]
        if values:
            rule[key] = values
    return rule
