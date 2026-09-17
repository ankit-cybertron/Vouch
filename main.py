#!/usr/bin/env python3
"""
Vouch — Review Confidence Scoring & Risk-Aware Re-Queuing

Entrypoint script for running the Vouch review intelligence system.
Usage:
    python main.py                    # Start web dashboard on port 5001
    python main.py --port 5000        # Custom port
    python main.py --host 127.0.0.1   # Custom host
    python main.py --debug            # Enable debug mode
"""

from __future__ import annotations

import argparse
import os
import sys

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load environment variables from .env
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
except ImportError:
    env_file = os.path.join(PROJECT_ROOT, ".env")
    if os.path.exists(env_file):
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip("\"'"))

# Expose Flask WSGI application
from dashboard.app import app  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vouch: Review confidence scoring and risk-aware PR re-queuing engine.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--host",
        type=str,
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host address to bind the web server to.",
    )
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 5001)),
        help="Port to run the dashboard server on.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=os.environ.get("DEBUG", "0").lower() in ("1", "true", "yes"),
        help="Enable Flask debug mode.",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable Flask auto-reloader.",
    )
    return parser.parse_args()


def print_banner(host: str, port: int, debug: bool) -> None:
    display_host = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    print("=" * 68)
    print("  VOUCH — PR Review Confidence & Risk Intelligence Engine")
    print("=" * 68)
    print(f"  Web Dashboard:       http://{display_host}:{port}/")
    print(f"  Repositories:        http://{display_host}:{port}/repos")
    print(f"  Validation Suite:    http://{display_host}:{port}/validation")
    print(f"  Binding Address:     http://{host}:{port}")
    print(f"  Debug Mode:          {'ON' if debug else 'OFF'}")
    print("=" * 68)
    print("  Press Ctrl+C to shut down server.\n")


def main() -> None:
    args = parse_args()
    print_banner(args.host, args.port, args.debug)
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        use_reloader=not args.no_reload,
    )


if __name__ == "__main__":
    main()
