"""Bucle principal: sigue la ventana activa, detecta ausencias y lanza el OCR periódicamente.

Puede ejecutarse en primer plano (consola) o en un hilo, controlado con pause()/resume()/stop().
"""

import logging
import threading
import time
from dataclasses import dataclass

from . import win32
from .browser import BrowserUrls, domain
from .capture import WindowCapturer, visual_change
from .config import Config
from .ocr import WindowsOcr
from .presence import media_in_use
from .privacy import PrivacyFilter
from .rules import RuleSet
from .store import AWAY, Activity, Store
from .titles import clean_title
from .topics import TopicExtractor

log = logging.getLogger("timetrack")

_LOCK_PROCESSES = {"lockapp.exe", "logonui.exe"}
# Interfaz del sistema que roba el foco un instante (bandeja, notificaciones, Inicio, Alt+Tab):
# no abre actividad nueva, el tiempo sigue contando para la anterior.
_TRANSIENT_PROCESSES = {"shellexperiencehost.exe", "startmenuexperiencehost.exe",
                        "searchhost.exe", "searchapp.exe", "shellhost.exe"}
_TRANSIENT_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "NotifyIconOverflowWindow",
                      "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow",
                      "MultitaskingViewFrame", "ForegroundStaging", "TaskSwitcherWnd"}
_FLUSH_EVERY_S = 5.0
# La URL se relee al cambiar de ventana o de título y, si no, cada tanto (webs de una página).
_URL_EVERY_S = 3.0
# El registro del micrófono y la cámara solo se mira cuando ya hay un rato sin teclado ni ratón.
_MEDIA_CHECK_AFTER_S = 30.0


@dataclass
class TrackerStatus:
    running: bool
    paused: bool
    process: str = ""
    title: str = ""
    topic: str = ""
    url: str = ""
    task: str = ""
    since: float = 0.0
    ocr_language: str = ""
    error: str = ""


class Tracker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.s = cfg.settings
        self.privacy = PrivacyFilter(cfg.privacy)
        self.rules = RuleSet(cfg.rules)
        self.topics = TopicExtractor(cfg.topic)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._running = False
        self._error = ""
        self._ocr_language = ""
        # store, capturer y ocr se crean en el hilo que ejecuta run(): mss y sqlite son por hilo.
        self.store: Store | None = None
        self._reset_activity_state()
        self._key: tuple[str, str, str] | None = None
        self._urls: BrowserUrls | None = None
        self._url_cache: tuple[int, str, float, str] = (0, "", 0.0, "")   # hwnd, título, ts, url
        self._activity: Activity | None = None
        self._media_ts = 0.0   # último instante con el micrófono o la cámara en uso

    # --- control -------------------------------------------------------------------------

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def stop(self) -> None:
        self._stop.set()

    def reload_rules(self, raw_rules: list[dict]) -> None:
        self.rules = RuleSet(raw_rules)

    def status(self) -> TrackerStatus:
        act = self._activity
        return TrackerStatus(
            running=self._running, paused=self._paused.is_set(),
            process=act.process if act else "", title=act.title if act else "",
            topic=act.topic if act else "", url=act.url if act else "",
            task=self.rules.classify(act) if act else "", since=act.start_ts if act else 0.0,
            ocr_language=self._ocr_language, error=self._error)

    # --- bucle ---------------------------------------------------------------------------

    def run(self) -> None:
        try:
            self.store = Store(self.cfg.db_path)
            self.capturer = WindowCapturer()
            self.ocr = WindowsOcr(self.s.ocr_language)
        except Exception as exc:
            self._error = str(exc)
            log.exception("No se pudo iniciar el seguimiento")
            raise
        try:
            self._urls = BrowserUrls()
        except Exception:
            log.exception("No se podrán leer las URL de los navegadores")
        self._ocr_language = self.ocr.language
        self._running = True
        log.info("Seguimiento iniciado. OCR: %s, cada %ss.", self.ocr.language, self.s.ocr_interval_s)
        failures = 0
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                try:
                    if self._paused.is_set():
                        self._close_activity(time.time())
                    else:
                        self._tick(time.time())
                    failures = 0
                except Exception as exc:
                    # Un fallo puntual (una ventana que se cierra a mitad de lectura, la base de
                    # datos ocupada…) no debe parar el seguimiento: se registra y se sigue.
                    failures += 1
                    self._error = f"{type(exc).__name__}: {exc}" if failures >= 5 else ""
                    if failures <= 3 or failures % 60 == 0:
                        log.exception("Fallo en el ciclo de seguimiento (%d seguidos)", failures)
                self._stop.wait(max(0.0, self.s.poll_interval_s - (time.monotonic() - started)))
        except KeyboardInterrupt:
            log.info("Parando…")
        finally:
            self._close_activity(time.time())
            self._running = False
            self.capturer.close()
            self.store.close()

    def _tick(self, now: float) -> None:
        idle_s = self._idle_seconds(now)
        fg = win32.get_foreground_window()
        locked = fg is None or fg.process.lower() in _LOCK_PROCESSES

        if locked or idle_s >= self.s.idle_threshold_s:
            # Al pasar a ausente por inactividad, el corte real fue la última entrada del usuario.
            boundary = now - idle_s if not locked and self._key != (AWAY, "", "") else now
            self._switch_to((AWAY, "", ""), "", boundary)
            self._maybe_flush(now)
            return

        if self._activity and (fg.process.lower() in _TRANSIENT_PROCESSES
                               or fg.class_name in _TRANSIENT_CLASSES):
            self._maybe_flush(now)
            return

        url = self._url_for(fg, now)
        self._switch_to((fg.process, clean_title(fg.title), domain(url)), fg.exe_path, now, url)
        self._maybe_flush(now)
        self._maybe_ocr(fg, now)

    def _idle_seconds(self, now: float) -> float:
        """Tiempo sin teclado ni ratón; una llamada (micrófono o cámara en uso) cuenta como
        actividad, así que una reunión en la que no se toca nada no pasa a ausente."""
        idle_s = win32.seconds_since_last_input()
        if not self.s.media_keeps_present or idle_s < _MEDIA_CHECK_AFTER_S:
            return idle_s
        try:
            if media_in_use():
                if now - self._media_ts > 60:
                    log.info("  Micrófono o cámara en uso: se mantiene como presente")
                self._media_ts = now
        except Exception:
            log.exception("No se pudo comprobar el uso del micrófono o la cámara")
        # Al colgar, la ausencia empieza a contar desde el final de la llamada, no desde la
        # última tecla pulsada.
        return min(idle_s, now - self._media_ts)

    def _close_activity(self, ts: float) -> None:
        if self._activity:
            self.store.extend_activity(self._activity.id, max(ts, self._activity.start_ts))
            log.info("Pausado" if self._paused.is_set() else "Actividad cerrada")
        self._activity = None
        self._key = None

    def _reset_activity_state(self) -> None:
        self._switch_ts = 0.0
        self._last_flush = 0.0
        self._last_ocr_ts: float | None = None
        self._last_fingerprint = None
        self._last_text = ""

    def _url_for(self, fg: win32.ForegroundWindow, now: float) -> str:
        if not self._urls or not self.privacy.ocr_allowed(fg.process, fg.title):
            return ""   # en ventanas privadas tampoco se guarda la URL
        hwnd, title, ts, url = self._url_cache
        if hwnd == fg.hwnd and title == fg.title and now - ts < _URL_EVERY_S:
            return url
        read = self._urls.url(fg.hwnd, fg.process)
        if read is None:   # escribiendo en la barra de direcciones: se mantiene la anterior
            return url if hwnd == fg.hwnd else ""
        url = self.privacy.clean(read)
        self._url_cache = (fg.hwnd, fg.title, now, url)
        return url

    def _switch_to(self, key: tuple[str, str, str], exe_path: str, ts: float,
                   url: str = "") -> None:
        if key == self._key:
            return
        if self._activity:
            ts = max(ts, self._activity.start_ts)   # nunca solapar con la actividad anterior
            self._activity.end_ts = ts
            self.store.extend_activity(self._activity.id, ts)
        process, title, _ = key
        activity_id = self.store.start_activity(ts, process, exe_path, title, url=url)
        self._activity = Activity(activity_id, ts, ts, process, exe_path, title, url=url)
        self._key = key
        self._reset_activity_state()
        self._switch_ts = ts
        log.info("%s | %s%s → %s", process, title[:90] or "(sin título)",
                 f" [{key[2]}]" if key[2] else "",
                 self.rules.classify(self._activity))

    def _maybe_flush(self, now: float) -> None:
        if self._activity and now - self._last_flush >= _FLUSH_EVERY_S:
            self._activity.end_ts = now
            self.store.extend_activity(self._activity.id, now)
            self._last_flush = now

    def _maybe_ocr(self, fg: win32.ForegroundWindow, now: float) -> None:
        if fg.minimized or not self.privacy.ocr_allowed(fg.process, fg.title):
            return
        if now - self._switch_ts < self.s.ocr_after_switch_s:
            return   # espera a que el usuario se asiente en la ventana (evita OCR al hacer Alt+Tab)
        if self._last_ocr_ts is not None and now - self._last_ocr_ts < self.s.ocr_interval_s:
            return
        self._last_ocr_ts = now

        try:
            capture = self.capturer.capture(fg.hwnd)
            if capture is None:
                return
            if visual_change(self._last_fingerprint, capture.fingerprint) < self.s.change_threshold:
                return
            self._last_fingerprint = capture.fingerprint
            started = time.perf_counter()
            lines = self.ocr.recognize_lines(capture)
        except Exception:
            log.exception("Fallo al capturar o reconocer la ventana")
            return
        text = self.privacy.clean("\n".join(ln.text for ln in lines).strip())

        app = self.topics.app_for(fg.title, self._activity.url)
        if app:
            topic = self.privacy.clean(self.topics.extract(app, lines, capture.width, capture.height))
            self._apply_topic(topic, now)

        if not text or text == self._last_text:
            return
        self._last_text = text
        self.store.add_ocr(self._activity.id, now, text)
        self._activity.ocr_text = f"{self._activity.ocr_text}\n{text}".strip()
        log.info("  OCR %d caracteres en %.0f ms → %s", len(text),
                 (time.perf_counter() - started) * 1000, self.rules.classify(self._activity))

    def _apply_topic(self, topic: str, now: float) -> None:
        """Un cambio de tema en la misma ventana (otro correo abierto) parte la actividad."""
        act = self._activity
        if not topic or topic == act.topic:
            return
        if not act.topic:
            act.topic = topic
            self.store.set_topic(act.id, topic)
        else:
            act.end_ts = now
            self.store.extend_activity(act.id, now)
            new_id = self.store.start_activity(now, act.process, act.exe_path, act.title, topic,
                                               act.url)
            self._activity = Activity(new_id, now, now, act.process, act.exe_path, act.title,
                                      topic=topic, url=act.url)
            self._last_text = ""
        log.info("  Tema: %s → %s", topic[:90], self.rules.classify(self._activity))
