"""Talking entry point.

  python main.py serve [--host H --port P --ssl-certfile F --ssl-keyfile F | --self-signed]
  python main.py selftest
"""
from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser(prog="talking")
    sub = ap.add_subparsers(dest="cmd")
    sp = sub.add_parser("serve", help="run the FastAPI + WebSocket server")
    sp.add_argument("--host")
    sp.add_argument("--port", type=int)
    sp.add_argument("--ssl-certfile")
    sp.add_argument("--ssl-keyfile")
    sp.add_argument("--self-signed", action="store_true",
                    help="generate a LAN self-signed cert so a phone mic works over Wi-Fi")
    sub.add_parser("selftest", help="run pytest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        sys.exit(subprocess.call([sys.executable, "-m", "pytest", "-q", str(ROOT / "tests")]))  # nosec B603
    if a.cmd is None:
        a.host = None
        a.port = None
        a.ssl_certfile = None
        a.ssl_keyfile = None
        a.self_signed = False
    elif a.cmd != "serve":
        ap.print_help()
        sys.exit(2)

    import uvicorn

    from talking.certs import self_signed
    from talking.config import Settings
    from talking.server import create_app

    s = Settings()
    cert, key = a.ssl_certfile, a.ssl_keyfile
    if a.self_signed and not cert:
        cert, key = self_signed(Path(s.data_dir) / "certs")
    app = create_app(s)
    uvicorn.run(
        app,
        host=a.host or s.host,
        port=a.port or s.port,
        ssl_certfile=cert,
        ssl_keyfile=key,
        proxy_headers=os.environ.get("APP_TRUST_PROXY", "").lower() in ("1", "true"),
    )


if __name__ == "__main__":
    main()
