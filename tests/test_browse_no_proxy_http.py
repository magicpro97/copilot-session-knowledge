#!/usr/bin/env python3
"""tests/test_browse_no_proxy_http.py — verify explicit no-proxy opener usage."""

import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if os.name == "nt":
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[1]

PASS = 0
FAIL = 0


def test(name: str, expr: bool, detail: str = "") -> None:
    global PASS, FAIL
    if expr:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        suffix = f" — {detail}" if detail else ""
        print(f"  FAIL  {name}{suffix}")


def test_browse_init_uses_no_proxy_opener() -> None:
    content = (REPO / "browse" / "__init__.py").read_text(encoding="utf-8")
    test("browse/__init__.py: ProxyHandler({}) present", "ProxyHandler({})" in content)
    test("browse/__init__.py: no_proxy_opener.open used", "no_proxy_opener.open" in content)


def test_vendor_download_uses_no_proxy_opener() -> None:
    content = (REPO / "browse" / "static" / "vendor" / "_download.py").read_text(encoding="utf-8")
    test("_download.py: ProxyHandler({}) present", "ProxyHandler({})" in content)
    test("_download.py: no_proxy_opener.open used", "no_proxy_opener.open" in content)


def test_proxy_handler_empty_ignores_env_vars() -> None:
    saved_http = os.environ.get("HTTP_PROXY")
    saved_https = os.environ.get("HTTPS_PROXY")
    os.environ["HTTP_PROXY"] = "http://proxy.corp.example.com:8080"
    os.environ["HTTPS_PROXY"] = "http://proxy.corp.example.com:8080"
    try:
        default_ph = urllib.request.ProxyHandler()
        no_proxy_ph = urllib.request.ProxyHandler({})
        test(
            "default ProxyHandler() reads env proxy",
            "http" in default_ph.proxies and "proxy.corp.example.com" in default_ph.proxies.get("http", ""),
            detail=f"proxies={default_ph.proxies}",
        )
        test(
            "ProxyHandler({}) ignores env proxy",
            not no_proxy_ph.proxies or "http" not in no_proxy_ph.proxies,
            detail=f"proxies={no_proxy_ph.proxies}",
        )
    finally:
        if saved_http is None:
            os.environ.pop("HTTP_PROXY", None)
        else:
            os.environ["HTTP_PROXY"] = saved_http
        if saved_https is None:
            os.environ.pop("HTTPS_PROXY", None)
        else:
            os.environ["HTTPS_PROXY"] = saved_https


class _OKHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args) -> None:
        pass


def _make_local_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OKHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, server.server_address[1]


def test_no_proxy_opener_bypasses_dead_proxy_env() -> None:
    server, port = _make_local_server()
    try:
        target_url = f"http://127.0.0.1:{port}/"
        saved_http = os.environ.get("HTTP_PROXY")
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:19977"
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(target_url, timeout=3) as resp:
                status = resp.status
        finally:
            if saved_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = saved_http
        test("no_proxy_opener reaches local server", status == 200, detail=f"status={status}")
    finally:
        server.shutdown()


if __name__ == "__main__":
    print("\n=== No-proxy HTTP client tests (issue #70) ===\n")
    test_browse_init_uses_no_proxy_opener()
    test_vendor_download_uses_no_proxy_opener()
    test_proxy_handler_empty_ignores_env_vars()
    test_no_proxy_opener_bypasses_dead_proxy_env()
    print(f"\n  Results: {PASS} passed, {FAIL} failed\n")
    sys.exit(0 if FAIL == 0 else 1)
