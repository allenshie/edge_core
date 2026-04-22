"""Lightweight HTTP server to update edge operating mode."""
from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from smart_workflow import TaskContext

from edge.runtime.shutdown_summary import cleanup_record

LOGGER = logging.getLogger(__name__)
MODE_RESOURCE = "edge_mode"


class ModeRequestHandler(BaseHTTPRequestHandler):
    server: "ModeServer"  # type: ignore[assignment]

    def log_message(self, format: str, *args):  # noqa: A003
        LOGGER.debug("[mode-server] " + format, *args)

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/mode":
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self.send_error(400, "invalid json")
            return
        mode = str(data.get("mode", "")).strip().lower()
        if not mode:
            self.send_error(400, "missing mode")
            return
        self.server.context.set_resource(MODE_RESOURCE, mode)
        LOGGER.info("收到 mode 更新：%s", mode)
        self.send_response(204)
        self.end_headers()


class ModeServer(ThreadingHTTPServer):
    def __init__(self, host: str, port: int, context: TaskContext):
        super().__init__((host, port), ModeRequestHandler)
        self.context = context


def start_mode_server(host: str, port: int, context: TaskContext) -> ModeServer:
    server = ModeServer(host, port, context)
    thread = threading.Thread(target=server.serve_forever, name="EdgeModeServer", daemon=True)
    thread.start()
    LOGGER.info("mode server listening on %s:%s", host, port)
    return server


def stop_mode_server(server: ModeServer | None) -> list[dict]:
    if server is None:
        return [
            cleanup_record(
                item="mode.server",
                type="server",
                state="skipped",
                ok=True,
                alive_before=False,
                alive_after=False,
                detail="mode server disabled",
            )
        ]
    try:
        server.shutdown()
        server.server_close()
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("mode server stop failed: %s", exc)
        return [
            cleanup_record(
                item="mode.server",
                type="server",
                state="failed",
                ok=False,
                alive_before=True,
                alive_after=True,
                detail="mode server stop failed",
                error=str(exc),
            )
        ]
    return [
        cleanup_record(
            item="mode.server",
            type="server",
            state="done",
            ok=True,
            alive_before=True,
            alive_after=False,
            detail="mode server stopped",
        )
    ]
