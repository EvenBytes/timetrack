"""timetrack — seguimiento de actividad con OCR y clasificación por reglas.

  timetrack app                    icono en la bandeja + registro + panel web (por defecto)
  timetrack panel                  solo el panel web, sin registrar
  timetrack track                  registrar en esta consola (Ctrl+C para parar)
  timetrack report [--date D]      resumen por tarea del día
  timetrack timeline [--date D]    bloques de tiempo fusionados
  timetrack unclassified           lo que más tiempo ocupa sin regla
  timetrack export --out f.csv     actividades clasificadas en CSV
  timetrack ocr-test [--delay 3]   OCR de la ventana activa (para probar)
"""

import argparse
import logging
import sys
import threading
import time
from datetime import date

from . import win32
from .config import load_config


def main(argv: list[str] | None = None) -> int:
    has_console = sys.stdout is not None   # el .exe con ventana (sin consola) no tiene stdout
    if has_console:
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="timetrack", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("app")
    sub.add_parser("panel")
    sub.add_parser("track")
    for name in ("report", "timeline", "unclassified", "export"):
        p = sub.add_parser(name)
        p.add_argument("--date", type=date.fromisoformat, default=date.today(),
                       help="YYYY-MM-DD (por defecto, hoy)")
        if name == "report":
            p.add_argument("--away", action="store_true", help="incluir el tiempo ausente")
        if name == "export":
            p.add_argument("--out", help="fichero CSV (por defecto, stdout)")
    p = sub.add_parser("ocr-test")
    p.add_argument("--delay", type=float, default=3.0, help="segundos para cambiar de ventana")
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv or ["app"])   # doble clic en el .exe = modo aplicación

    win32.enable_dpi_awareness()
    cfg = load_config()
    _setup_logging(cfg, to_file=args.cmd in ("app", "panel"), to_console=has_console)

    if args.cmd == "app":
        from .tray import run_app
        return run_app(cfg)

    if args.cmd == "panel":
        from .web import Panel
        panel = Panel(cfg)
        print(f"Panel en {panel.url} (Ctrl+C para parar)")
        try:
            panel.serve_forever()
        except KeyboardInterrupt:
            pass
        return 0

    if args.cmd == "track":
        from .tracker import Tracker
        Tracker(cfg).run()
        return 0

    if args.cmd == "ocr-test":
        return _ocr_test(cfg, args.delay)

    from . import report
    from .config import load_rules
    from .rules import OTHER_CATEGORY, RuleSet
    from .store import Store
    store = Store(cfg.db_path)
    categories = RuleSet(load_rules(cfg.categories_path), OTHER_CATEGORY)
    items = report.classified(store, RuleSet(cfg.rules), args.date, categories=categories)
    if args.cmd == "report":
        report.summary(items, include_away=args.away)
    elif args.cmd == "timeline":
        report.print_timeline(report.timeline(items))
    elif args.cmd == "unclassified":
        report.unclassified(items)
    elif args.cmd == "export":
        if args.out:
            with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
                report.export_csv(items, f)
        else:
            report.export_csv(items, None)
    return 0


def _setup_logging(cfg, to_file: bool, to_console: bool) -> None:
    handlers: list[logging.Handler] = []
    if to_console:
        handlers.append(logging.StreamHandler())
    if to_file or not to_console:
        from logging.handlers import RotatingFileHandler
        cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(cfg.db_path.parent / "timetrack.log",
                                            maxBytes=2_000_000, backupCount=2, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", handlers=handlers)
    logging.getLogger("comtypes").setLevel(logging.WARNING)   # avisos internos de su caché

    # Sin consola (el .exe) las excepciones no capturadas de un hilo se perderían: al log.
    def log_thread_exception(args):
        logging.getLogger("timetrack").error(
            "Error no capturado en el hilo %s", args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    threading.excepthook = log_thread_exception


def _ocr_test(cfg, delay: float) -> int:
    from .capture import WindowCapturer
    from .ocr import WindowsOcr, available_languages
    from .privacy import PrivacyFilter

    print(f"Idiomas OCR instalados: {', '.join(available_languages()) or 'ninguno'}")
    print(f"Cambia a la ventana que quieras probar; capturo en {delay:.0f} s…")
    time.sleep(delay)
    fg = win32.get_foreground_window()
    if fg is None:
        print("No hay ventana activa.")
        return 1
    capture = WindowCapturer().capture(fg.hwnd)
    if capture is None:
        print("No se pudo capturar la ventana.")
        return 1
    engine = WindowsOcr(cfg.settings.ocr_language)
    started = time.perf_counter()
    text = PrivacyFilter(cfg.privacy).clean(engine.recognize(capture))
    print(f"\n{fg.process} | {fg.title}\n{capture.width}x{capture.height} px, idioma "
          f"{engine.language}, {(time.perf_counter() - started) * 1000:.0f} ms\n{'-' * 60}")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
