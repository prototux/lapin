"""python -m assistant [--data DIR]"""

import argparse
import logging
import signal
import sys
import threading

from werkzeug.serving import make_server

from .app import App
from .webui import create_app


def main():
    ap = argparse.ArgumentParser(description="Voice assistant server")
    ap.add_argument("--data", default="data", help="data directory (settings, database)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname).1s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    for noisy in ("werkzeug", "websockets", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    app = App(args.data)
    app.start()
    s = app.settings["server"]
    web = make_server(s["web_host"], s["web_port"], create_app(app), threaded=True)
    threading.Thread(target=web.serve_forever, name="web", daemon=True).start()
    logging.info("admin web UI on http://%s:%d", s["web_host"], s["web_port"])
    threading.Thread(target=app.check_health, daemon=True).start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()
    logging.info("stopping")
    web.shutdown()
    app.gateway.stop()


if __name__ == "__main__":
    main()
