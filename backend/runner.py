"""Uvicorn runner with an authenticated graceful-shutdown hook for Tauri."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import threading
from contextlib import suppress

import uvicorn

from backend.api.main import app


async def _serve(host: str, port: int, shutdown_on_stdin_close: bool = False) -> None:
    shutdown_requested = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown():
        with suppress(RuntimeError):  # The event loop may already have closed.
            loop.call_soon_threadsafe(shutdown_requested.set)

    app.state.request_shutdown = request_shutdown
    if shutdown_on_stdin_close:
        def watch_host():
            # Tauri keeps the write end open for the lifetime of the desktop process.
            while os.read(sys.stdin.fileno(), 1):
                pass
            request_shutdown()

        threading.Thread(target=watch_host, name="desktop-host-watch", daemon=True).start()

    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=host,
            port=port,
            access_log=False,
            log_level="info",
        )
    )

    async def watch_shutdown() -> None:
        await shutdown_requested.wait()
        server.should_exit = True

    watcher = asyncio.create_task(watch_shutdown())
    try:
        await server.serve()
    finally:
        watcher.cancel()
        try:
            await watcher
        except asyncio.CancelledError:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI Blogger Studio local API sidecar.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--shutdown-on-stdin-close", action="store_true")
    args = parser.parse_args()
    asyncio.run(_serve(args.host, args.port, args.shutdown_on_stdin_close))


if __name__ == "__main__":
    main()
