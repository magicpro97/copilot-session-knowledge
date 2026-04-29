"""browse/core/streaming.py — SSE streaming helper with heartbeat + stop flag."""

import os
import sys
import threading

if os.name == "nt":
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")


def sse_response(
    handler,
    generator,
    heartbeat: int = 15,
    stop_event: threading.Event | None = None,
) -> threading.Event:
    """
    Stream Server-Sent Events to the HTTP client.

    - handler: BaseHTTPRequestHandler instance (must have send_response,
               send_header, end_headers, wfile).
    - generator: iterable yielding string chunks (one SSE data value each).
    - heartbeat: seconds between keep-alive comments (default 15).
    - stop_event: external threading.Event to signal early shutdown.
                  Created internally if not provided; returned either way.

    Returns the stop_event so the caller can signal early shutdown.
    Each yielded chunk is framed as:  data: <chunk>\\n\\n
    """
    if stop_event is None:
        stop_event = threading.Event()

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.end_headers()

    def _heartbeat_loop() -> None:
        while not stop_event.wait(heartbeat):
            try:
                handler.wfile.write(b": heartbeat\n\n")
                handler.wfile.flush()
            except (BrokenPipeError, OSError):
                stop_event.set()
                return

    hb = threading.Thread(target=_heartbeat_loop, daemon=True)
    hb.start()

    try:
        for chunk in generator:
            if stop_event.is_set():
                break
            msg = f"data: {chunk}\n\n".encode()
            try:
                handler.wfile.write(msg)
                handler.wfile.flush()
            except (BrokenPipeError, OSError):
                break
    finally:
        stop_event.set()

    return stop_event
