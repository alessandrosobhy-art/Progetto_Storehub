from __future__ import annotations

from app_logging import log_swallowed
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict

import requests
from flask import Response, current_app, g, jsonify, request

from app_db import get_connection_sqlserver_database, get_storehub_database_name


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class _ControllerMonitor:
    def __init__(self, app_name: str, version: str, slow_ms: int = 3000):
        self.app_name = str(app_name or "fp").strip() or "fp"
        self.version = str(version or "unknown").strip() or "unknown"
        self.slow_ms = max(250, int(slow_ms or 3000))
        self.started_at = time.time()
        self.started_at_iso = _utc_now_iso()
        self.lock = threading.Lock()
        self.total_requests = 0
        self.total_errors = 0
        self.total_slow_requests = 0
        self.active_requests = 0
        self.peak_active_requests = 0
        self.total_duration_ms = 0.0
        self.last_request_at = ""
        self.last_error_at = ""
        self.last_slow_at = ""
        self.recent_requests: deque[Dict[str, Any]] = deque(maxlen=50)
        self.endpoint_stats: Dict[str, Dict[str, Any]] = {}
        self.dependency_cache: Dict[str, Any] = {
            "expires_at": 0.0,
            "payload": {},
        }
        self.heartbeat_state: Dict[str, Any] = {
            "enabled": False,
            "running": False,
            "interval_seconds": 0,
            "last_run_at": "",
            "last_success_at": "",
            "last_error": "",
            "target_url": "",
        }
        self._heartbeat_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def request_started(self) -> None:
        with self.lock:
            self.active_requests += 1
            if self.active_requests > self.peak_active_requests:
                self.peak_active_requests = self.active_requests

    def request_finished(
        self,
        *,
        endpoint: str,
        path: str,
        method: str,
        status_code: int,
        duration_ms: float,
    ) -> None:
        now_iso = _utc_now_iso()
        row = {
            "ts": now_iso,
            "endpoint": endpoint,
            "path": path,
            "method": method,
            "status_code": int(status_code or 0),
            "duration_ms": round(float(duration_ms or 0.0), 2),
        }
        key = f"{method} {endpoint or path}"
        with self.lock:
            self.total_requests += 1
            self.total_duration_ms += float(duration_ms or 0.0)
            self.last_request_at = now_iso
            self.recent_requests.append(row)

            stat = self.endpoint_stats.setdefault(
                key,
                {
                    "endpoint": endpoint,
                    "path": path,
                    "method": method,
                    "count": 0,
                    "errors": 0,
                    "slow": 0,
                    "total_duration_ms": 0.0,
                    "max_duration_ms": 0.0,
                    "last_status_code": 0,
                    "last_seen_at": "",
                },
            )
            stat["count"] += 1
            stat["total_duration_ms"] += float(duration_ms or 0.0)
            stat["max_duration_ms"] = max(float(stat["max_duration_ms"] or 0.0), float(duration_ms or 0.0))
            stat["last_status_code"] = int(status_code or 0)
            stat["last_seen_at"] = now_iso

            if int(status_code or 0) >= 500:
                self.total_errors += 1
                self.last_error_at = now_iso
                stat["errors"] += 1
            if float(duration_ms or 0.0) >= self.slow_ms:
                self.total_slow_requests += 1
                self.last_slow_at = now_iso
                stat["slow"] += 1

    def request_cleanup(self) -> None:
        with self.lock:
            self.active_requests = max(0, self.active_requests - 1)

    def unhandled_exception(self) -> None:
        now_iso = _utc_now_iso()
        with self.lock:
            self.total_errors += 1
            self.last_error_at = now_iso

    def _check_sql(self) -> Dict[str, Any]:
        started = time.perf_counter()
        database_name = get_storehub_database_name()
        try:
            conn = get_connection_sqlserver_database(database_name, read_only=True)
            try:
                cur = conn.cursor()
                cur.execute("SELECT 1")
                cur.fetchone()
            finally:
                try:
                    conn.close()
                except Exception:
                    log_swallowed('controller_monitoring:139')
            return {
                "status": "ok",
                "database": database_name,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }
        except Exception as exc:
            return {
                "status": "error",
                "database": database_name,
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "error": str(exc),
            }

    def _check_supabase(self) -> Dict[str, Any]:
        from db_integration import SUPABASE_URL, _sb_headers, _session

        started = time.perf_counter()
        if not SUPABASE_URL:
            return {
                "status": "error",
                "latency_ms": 0.0,
                "error": "SUPABASE_URL non configurato",
            }
        try:
            response = _session.get(
                f"{SUPABASE_URL}/rest/v1/warehouse_stores",
                headers=_sb_headers(False),
                params={"select": "code", "limit": "1"},
                timeout=10,
            )
            return {
                "status": "ok" if response.ok else "error",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "http_status": int(response.status_code),
                "url": SUPABASE_URL,
                **({"error": response.text[:300]} if not response.ok else {}),
            }
        except Exception as exc:
            return {
                "status": "error",
                "latency_ms": round((time.perf_counter() - started) * 1000.0, 2),
                "url": SUPABASE_URL,
                "error": str(exc),
            }

    def dependency_snapshot(self, force: bool = False) -> Dict[str, Any]:
        now = time.time()
        with self.lock:
            if not force and self.dependency_cache.get("expires_at", 0.0) > now:
                payload = self.dependency_cache.get("payload") or {}
                return dict(payload)

        payload = {
            "sql_server": self._check_sql(),
            "supabase": self._check_supabase(),
        }
        with self.lock:
            self.dependency_cache = {
                "expires_at": now + 20.0,
                "payload": payload,
            }
        return dict(payload)

    def metrics_payload(self) -> Dict[str, Any]:
        deps = self.dependency_snapshot(force=False)
        with self.lock:
            avg_ms = (self.total_duration_ms / self.total_requests) if self.total_requests else 0.0
            top_endpoints = sorted(
                self.endpoint_stats.values(),
                key=lambda row: (float(row.get("max_duration_ms") or 0.0), float(row.get("total_duration_ms") or 0.0)),
                reverse=True,
            )[:10]
            app_status = "ok"
            if any((deps.get(name) or {}).get("status") != "ok" for name in ("sql_server", "supabase")):
                app_status = "degraded"
            return {
                "app": {
                    "name": self.app_name,
                    "status": app_status,
                    "version": self.version,
                    "started_at": self.started_at_iso,
                    "uptime_seconds": int(max(0.0, time.time() - self.started_at)),
                    "pid": os.getpid(),
                },
                "requests": {
                    "total": int(self.total_requests),
                    "errors": int(self.total_errors),
                    "slow_requests": int(self.total_slow_requests),
                    "active": int(self.active_requests),
                    "peak_active": int(self.peak_active_requests),
                    "average_duration_ms": round(avg_ms, 2),
                    "slow_threshold_ms": int(self.slow_ms),
                    "last_request_at": self.last_request_at,
                    "last_error_at": self.last_error_at,
                    "last_slow_at": self.last_slow_at,
                },
                "dependencies": deps,
                "jobs": {
                    "heartbeat": dict(self.heartbeat_state),
                },
                "recent_requests": list(self.recent_requests),
                "top_endpoints": [
                    {
                        **row,
                        "avg_duration_ms": round(
                            (float(row.get("total_duration_ms") or 0.0) / float(row.get("count") or 1)),
                            2,
                        ),
                    }
                    for row in top_endpoints
                ],
            }

    def _heartbeat_worker(self, target_url: str, token: str, interval_seconds: int) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Controller-Token": token,
        }
        self.heartbeat_state.update(
            {
                "enabled": True,
                "running": True,
                "interval_seconds": interval_seconds,
                "target_url": target_url,
            }
        )
        session = requests.Session()
        while not self._stop_event.wait(interval_seconds):
            self.heartbeat_state["last_run_at"] = _utc_now_iso()
            payload = self.metrics_payload()
            payload["heartbeat_source"] = self.app_name
            try:
                response = session.post(target_url, json=payload, headers=headers, timeout=10)
                if response.ok:
                    self.heartbeat_state["last_success_at"] = _utc_now_iso()
                    self.heartbeat_state["last_error"] = ""
                else:
                    self.heartbeat_state["last_error"] = f"HTTP {response.status_code}: {response.text[:200]}"
            except Exception as exc:
                self.heartbeat_state["last_error"] = str(exc)
        self.heartbeat_state["running"] = False

    def start_heartbeat(self) -> None:
        enabled = str(os.getenv("CONTROLLER_HEARTBEAT_ENABLED") or "").strip().lower() in {"1", "true", "yes", "y"}
        base_url = str(os.getenv("CONTROLLER_BASE_URL") or "").strip().rstrip("/")
        token = str(os.getenv("CONTROLLER_TOKEN") or "").strip()
        interval_seconds = max(15, int(os.getenv("CONTROLLER_HEARTBEAT_INTERVAL") or "60"))
        app_slug = str(os.getenv("CONTROLLER_HEARTBEAT_APP") or self.app_name).strip().lower() or self.app_name

        if not enabled:
            self.heartbeat_state["enabled"] = False
            return
        if not base_url or not token:
            self.heartbeat_state.update(
                {
                    "enabled": True,
                    "running": False,
                    "interval_seconds": interval_seconds,
                    "last_error": "CONTROLLER_BASE_URL o CONTROLLER_TOKEN mancanti",
                    "target_url": "",
                }
            )
            return
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        target_url = f"{base_url}/api/heartbeat/{app_slug}"
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_worker,
            args=(target_url, token, interval_seconds),
            name=f"{self.app_name}-controller-heartbeat",
            daemon=True,
        )
        self._heartbeat_thread.start()


def register_controller_monitoring(app, *, app_name: str, version: str) -> _ControllerMonitor:
    monitor = _ControllerMonitor(
        app_name=app_name,
        version=version,
        slow_ms=int(os.getenv("CONTROLLER_METRICS_SLOW_MS") or "3000"),
    )

    @app.before_request
    def _controller_metrics_before_request():
        g._controller_metrics_started_at = time.perf_counter()
        g._controller_metrics_after_ran = False
        monitor.request_started()
        return None

    @app.after_request
    def _controller_metrics_after_request(response: Response):
        started = getattr(g, "_controller_metrics_started_at", None)
        duration_ms = ((time.perf_counter() - started) * 1000.0) if started else 0.0
        monitor.request_finished(
            endpoint=str(request.endpoint or ""),
            path=str(request.path or ""),
            method=str(request.method or ""),
            status_code=int(getattr(response, "status_code", 0) or 0),
            duration_ms=duration_ms,
        )
        g._controller_metrics_after_ran = True
        return response

    @app.teardown_request
    def _controller_metrics_teardown_request(exc):
        if exc is not None and not getattr(g, "_controller_metrics_after_ran", False):
            monitor.unhandled_exception()
        monitor.request_cleanup()
        return None

    @app.get("/controller/metrics")
    def controller_metrics():
        return jsonify(monitor.metrics_payload())

    if not app.config.get("CONTROLLER_MONITORING_HEARTBEAT_STARTED"):
        app.config["CONTROLLER_MONITORING_HEARTBEAT_STARTED"] = True
        monitor.start_heartbeat()

    return monitor
