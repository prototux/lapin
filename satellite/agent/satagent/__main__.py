"""python3 -m satagent [--data-dir DIR] [--port N]"""

import argparse
import logging
import os
import signal
import sys
import threading

from werkzeug.serving import make_server

from .agent import Agent
from .config import Config
from .webui import create_app


def main():
    ap = argparse.ArgumentParser(description="Voice assistant satellite agent")
    ap.add_argument("--data-dir", default=os.path.expanduser("~/.local/share/satellite"))
    ap.add_argument("--port", type=int, default=None, help="web UI port (default from config: 8080)")
    ap.add_argument("--server", default=None, help="server URL (ws://host:8765/v1/device), saved")
    ap.add_argument("--socket", default=None, help="engine socket path, saved")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname).1s %(name)s: %(message)s", stream=sys.stdout)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    cfg = Config(args.data_dir)
    if args.server:
        cfg.update({"server_url": args.server})
    if args.socket:
        cfg.update({"engine_socket": args.socket})

    agent = Agent(cfg)
    agent.start()
    web = cfg["webui"]
    port = args.port or web.get("port", 8080)
    server = make_server(web.get("host", "0.0.0.0"), port, create_app(agent), threaded=True)
    logging.info("web UI on http://%s:%d  (device %s, server %s)", web.get("host"), port,
                 cfg["device_id"], cfg["server_url"])

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop.wait()
    logging.info("stopping")
    agent.stop()
    server.shutdown()


if __name__ == "__main__":
    main()
