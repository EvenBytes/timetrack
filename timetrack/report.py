"""Informes: resumen por tarea y por tipo de aplicación, línea temporal fusionada, pendientes de clasificar y CSV.

Las funciones de datos las usan tanto la consola como el panel web."""

import csv
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta

from .rules import AWAY_TASK, MEETING_CATEGORY, UNCLASSIFIED, RuleSet
from .store import Activity, Store

# Dos bloques de la misma tarea separados por menos de esto se fusionan en la línea temporal.
_MERGE_GAP_S = 120

Classified = list[tuple[Activity, str]]


@dataclass
class Block:
    task: str
    start_ts: float
    end_ts: float
    apps: dict[str, float] = field(default_factory=lambda: defaultdict(float))

    @property
    def duration(self) -> float:
        return self.end_ts - self.start_ts


@dataclass
class TaskTotal:
    task: str
    seconds: float
    apps: dict[str, float]
    categories: dict[str, float] = field(default_factory=dict)


@dataclass
class CategoryTotal:
    category: str
    seconds: float
    tasks: dict[str, float]
    apps: dict[str, float]


# --- datos -------------------------------------------------------------------------------

def day_bounds(day: date) -> tuple[float, float]:
    start = datetime.combine(day, dtime.min)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def classified(store: Store, rules: RuleSet, day: date, end_day: date | None = None,
               categories: RuleSet | None = None) -> Classified:
    """Actividades de [day, end_day] (ambos incluidos) con su tarea según las reglas actuales
    y, si se dan reglas de tipos, su tipo de aplicación en act.category."""
    start_ts = day_bounds(day)[0]
    end_ts = day_bounds(end_day or day)[1]
    items = [(a, rules.classify(a)) for a in store.activities_between(start_ts, end_ts)
             if a.duration > 0]
    if categories:
        for act, _ in items:
            # Con el micrófono o la cámara en uso se está en una llamada, sea cual sea la ventana
            # (el chat de Teams, el documento que se comparte…).
            act.category = (MEETING_CATEGORY if act.in_call and not act.is_away
                            else categories.classify(act))
    return items


def task_totals(items: Classified, include_away: bool = False) -> list[TaskTotal]:
    totals: dict[str, float] = defaultdict(float)
    apps: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    cats: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for act, task in items:
        if task == AWAY_TASK and not include_away:
            continue
        totals[task] += act.duration
        apps[task][act.app] += act.duration
        if act.category:
            cats[task][act.category] += act.duration
    return [TaskTotal(t, s, _desc(apps[t]), _desc(cats[t]))
            for t, s in sorted(totals.items(), key=lambda kv: -kv[1])]


def category_totals(items: Classified) -> list[CategoryTotal]:
    """Tiempo por tipo de aplicación (Correo, Chats…), con el reparto por tarea de cada uno."""
    totals: dict[str, float] = defaultdict(float)
    tasks: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    apps: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for act, task in items:
        if task == AWAY_TASK or not act.category:
            continue
        totals[act.category] += act.duration
        tasks[act.category][task] += act.duration
        apps[act.category][act.app] += act.duration
    return [CategoryTotal(c, s, _desc(tasks[c]), _desc(apps[c]))
            for c, s in sorted(totals.items(), key=lambda kv: -kv[1])]


def _desc(d: dict[str, float]) -> dict[str, float]:
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def worked_seconds(items: Classified) -> float:
    return sum(a.duration for a, task in items if task != AWAY_TASK)


def timeline(items: Classified, by: str = "task") -> list[Block]:
    """Bloques consecutivos de la misma tarea (by="task") o del mismo tipo (by="category")."""
    blocks: list[Block] = []
    for act, task in items:
        if by == "category":
            task = task if task == AWAY_TASK else act.category
        last = blocks[-1] if blocks else None
        if last and last.task == task and act.start_ts - last.end_ts <= _MERGE_GAP_S:
            last.end_ts = max(last.end_ts, act.end_ts)
        else:
            last = Block(task, act.start_ts, act.end_ts)
            blocks.append(last)
        last.apps[act.app] += act.duration
    return blocks


def unclassified_groups(items: Classified, top: int = 50) -> list[tuple[str, str, str, float]]:
    """Lo que más tiempo ocupa sin regla: el punto de partida para escribir reglas nuevas.
    Devuelve (aplicación, título, tema, segundos); en los navegadores la aplicación es el dominio."""
    groups: dict[tuple[str, str, str], float] = defaultdict(float)
    for act, task in items:
        if task == UNCLASSIFIED:
            groups[(act.app, act.title, act.topic)] += act.duration
    ranked = sorted(groups.items(), key=lambda kv: -kv[1])[:top]
    return [(proc, title, topic, secs) for (proc, title, topic), secs in ranked]


def export_csv(items: Classified, out, projects=None) -> None:
    writer = csv.writer(out or sys.stdout)
    writer.writerow(["inicio", "fin", "segundos", "cliente", "tarea", "imputable", "tipo",
                     "proceso", "titulo", "tema", "url", "ocr_extracto"])
    for act, task in items:
        client = projects.client_of(task) if projects else ""
        billable = ("si" if projects.billable(task) else "no") if projects else ""
        writer.writerow([_iso(act.start_ts), _iso(act.end_ts), round(act.duration), client, task,
                         billable, act.category, act.process, act.title, act.topic, act.url,
                         act.ocr_text[:300].replace("\n", " ")])


# --- consola -----------------------------------------------------------------------------

def summary(items: Classified, include_away: bool) -> None:
    worked = worked_seconds(items)
    print(f"\n{'Tarea':<40} {'Tiempo':>11} {'%':>6}   Aplicaciones principales")
    print("-" * 100)
    for t in task_totals(items, include_away):
        pct = f"{100 * t.seconds / worked:5.1f}" if worked and t.task != AWAY_TASK else "   - "
        top = ", ".join(f"{p} {_hm(s)}" for p, s in list(t.apps.items())[:3])
        print(f"{t.task[:40]:<40} {_hm(t.seconds):>11} {pct:>6}   {top}")
    print("-" * 100)
    print(f"{'Total trabajado':<40} {_hm(worked):>11}\n")
    cats = category_totals(items)
    if cats:
        print(f"{'Tipo de aplicación':<40} {'Tiempo':>11} {'%':>6}   Tareas principales")
        print("-" * 100)
        for c in cats:
            pct = f"{100 * c.seconds / worked:5.1f}" if worked else "   - "
            top = ", ".join(f"{t} {_hm(s)}" for t, s in list(c.tasks.items())[:3])
            print(f"{c.category[:40]:<40} {_hm(c.seconds):>11} {pct:>6}   {top}")
        print()


def print_timeline(blocks: list[Block], min_s: float = 60) -> None:
    for b in blocks:
        if b.duration < min_s:
            continue
        top = ", ".join(p for p, _ in sorted(b.apps.items(), key=lambda kv: -kv[1])[:3])
        print(f"{_clock(b.start_ts)}–{_clock(b.end_ts)}  {_hm(b.duration):>11}  "
              f"{b.task[:40]:<40} {top}")


def unclassified(items: Classified, top: int = 20) -> None:
    for proc, title, topic, secs in unclassified_groups(items, top):
        print(f"{_hm(secs):>11}  {proc:<25} {title[:90]}" + (f"  [tema: {topic[:60]}]" if topic else ""))


def _hm(secs: float) -> str:
    """45 s · 3 min · 1 h 5 min · 2 h"""
    if secs < 60:
        return f"{round(secs)} s"
    minutes = round(secs / 60)
    hours, rest = divmod(minutes, 60)
    if not hours:
        return f"{minutes} min"
    return f"{hours} h {rest} min" if rest else f"{hours} h"


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")
