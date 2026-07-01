import os
import sys
import time
import logging
import threading
import faulthandler
from logging.handlers import RotatingFileHandler


_RUNTIME_INITIALIZED = False


def _ensure_log_dir(log_dir: str) -> str:
    base = os.path.abspath(os.path.dirname(__file__))
    path = os.path.abspath(os.path.join(base, log_dir))
    os.makedirs(path, exist_ok=True)
    return path


def _parse_level(level_str: str) -> int:
    if not level_str:
        return logging.INFO
    s = str(level_str).strip().upper()
    return getattr(logging, s, logging.INFO)


def _install_excepthooks(logger: logging.Logger) -> None:
    def _sys_hook(exctype, value, tb):
        try:
            logger.critical('Unhandled exception', exc_info=(exctype, value, tb))
        finally:
            try:
                sys.__excepthook__(exctype, value, tb)
            except Exception:
                pass

    sys.excepthook = _sys_hook

    # Python 3.8+: exceptions in threads
    if hasattr(threading, 'excepthook'):
        def _thread_hook(args):
            logger.critical(
                'Unhandled thread exception (thread=%s)',
                getattr(args.thread, 'name', 'unknown'),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback)
            )

        threading.excepthook = _thread_hook  # type: ignore[attr-defined]


def _install_faulthandler(log_path: str) -> None:
    try:
        # Line buffered so we don’t lose the last lines on hard exits.
        fh = open(log_path, 'a', buffering=1, encoding='utf-8', errors='replace')
        faulthandler.enable(file=fh, all_threads=True)
    except Exception:
        # If this fails we still want the app to start.
        pass


def _setup_root_logging(log_dir: str, level: int) -> None:
    root = logging.getLogger()
    root.setLevel(level)

    # Avoid double handlers if reloaded.
    if getattr(root, '_magazzino_runtime_handlers', False):
        return

    fmt = logging.Formatter(
        '%(asctime)s | %(levelname)s | pid=%(process)d | %(threadName)s | %(name)s | %(message)s'
    )

    app_log = os.path.join(log_dir, 'app.log')
    err_log = os.path.join(log_dir, 'error.log')

    h1 = RotatingFileHandler(app_log, maxBytes=10 * 1024 * 1024, backupCount=10, encoding='utf-8')
    h1.setLevel(level)
    h1.setFormatter(fmt)

    h2 = RotatingFileHandler(err_log, maxBytes=10 * 1024 * 1024, backupCount=10, encoding='utf-8')
    h2.setLevel(logging.WARNING)
    h2.setFormatter(fmt)

    hs = logging.StreamHandler()
    hs.setLevel(level)
    hs.setFormatter(fmt)

    root.addHandler(h1)
    root.addHandler(h2)
    root.addHandler(hs)
    root._magazzino_runtime_handlers = True  # type: ignore[attr-defined]

    # Reduce noise (keep warnings/errors)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
    logging.getLogger('werkzeug').setLevel(logging.INFO)


def _install_request_metrics(app, logger: logging.Logger) -> None:
    try:
        from flask import g, request
    except Exception:
        return

    slow_ms = int(os.getenv('SLOW_REQUEST_MS', '2000'))

    @app.before_request
    def _rt__start():
        g._rt_start = time.time()

    @app.after_request
    def _rt__after(resp):
        try:
            start = getattr(g, '_rt_start', None)
            if start is None:
                return resp
            ms = int((time.time() - start) * 1000)

            # Log only slow or failing responses to avoid huge logs.
            if ms >= slow_ms or resp.status_code >= 500:
                logger.info(
                    '%s %s -> %s (%sms)',
                    request.method,
                    request.path,
                    resp.status_code,
                    ms
                )
        except Exception:
            pass
        return resp


def init_runtime(app=None, logger_name: str = 'app') -> logging.Logger:
    """Initialize:
    - rotating file logs in ./logs
    - faulthandler dump
    - unhandled exception hooks (main + threads)
    - optional slow-request logging

    Safe to call even if something fails.
    """
    global _RUNTIME_INITIALIZED
    if _RUNTIME_INITIALIZED:
        return logging.getLogger(logger_name)

    log_dir = _ensure_log_dir(os.getenv('LOG_DIR', 'logs'))
    level = _parse_level(os.getenv('LOG_LEVEL', 'INFO'))
    _setup_root_logging(log_dir, level)

    logger = logging.getLogger(logger_name)

    _install_faulthandler(os.path.join(log_dir, 'fault.log'))
    _install_excepthooks(logger)

    if app is not None:
        _install_request_metrics(app, logger)

    _RUNTIME_INITIALIZED = True
    logger.info('Runtime monitoring initialized (logs=%s, level=%s)', log_dir, logging.getLevelName(level))
    return logger
