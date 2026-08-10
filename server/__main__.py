"""Start the API.

    python -m server
    python -m server --port 9000 --reload
"""

from __future__ import annotations

import argparse
import logging

import uvicorn

from .settings import load_config


def main(argv=None) -> int:
    cfg = {}
    try:
        cfg = load_config()
    except (OSError, ValueError):
        # Reported properly, per request, by /api/health -- refusing to start here would
        # mean the one page that could tell you what is wrong never loads.
        pass
    server_cfg = cfg.get("server", {})

    parser = argparse.ArgumentParser(prog="python -m server", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=server_cfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(server_cfg.get("port", 8000)))
    parser.add_argument("--reload", action="store_true", help="restart on a code change")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    uvicorn.run("server.api:app", host=args.host, port=args.port, reload=args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
