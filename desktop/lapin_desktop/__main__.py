"""python -m lapin_desktop [--activate | --settings | --quit] [--headless]
                           [--text "..." | --wav file.wav] [--config-dir DIR]"""

import argparse
import logging
import os
import sys

from . import APP_NAME, i18n


def _record_crashes(directory):
    """A native crash (segfault in Qt, PortAudio...) writes every thread's
    Python stack to crash.log in the settings folder, kept short."""
    import faulthandler
    import time
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "crash.log")
        if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
            os.replace(path, path + ".old")
        f = open(path, "a", buffering=1)
        f.write("--- started %s, pid %d, Python %s, %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(),
                                                          sys.version.split()[0], sys.platform))
        faulthandler.enable(file=f, all_threads=True)
        _record_crashes.file = f            # keep it open for the process' life
    except OSError:
        faulthandler.enable()


def parse_args(argv=None):
    ap = argparse.ArgumentParser(prog="lapin-desktop", description="Lapin voice assistant for the desktop")
    ap.add_argument("--activate", action="store_true",
                    help="start talking in the running instance (bind this to a shortcut on Wayland)")
    ap.add_argument("--settings", action="store_true", help="open the settings of the running instance")
    ap.add_argument("--quit", action="store_true", help="quit the running instance")
    ap.add_argument("--headless", action="store_true", help="no overlay or tray icon (hotkey/--activate only)")
    ap.add_argument("--text", help="debug: send a typed request, print the reply and tool calls, exit")
    ap.add_argument("--wav", help="debug: send a WAV file as the microphone of a voice turn, print, exit")
    ap.add_argument("--confirm", choices=["yes", "no"], default="no",
                    help="debug: answer to a confirmation (default: no)")
    ap.add_argument("--speak", choices=["yes", "no"], help="debug --text: ask for a spoken answer or not")
    ap.add_argument("--no-audio", action="store_true", help="debug: play nothing (streams are only counted)")
    ap.add_argument("--earcon", metavar="NAME", help="play a sound (listen, done, error, notify, listen_end) and exit")
    ap.add_argument("--timeout", type=float, default=60, help="debug: give up after this many seconds")
    ap.add_argument("--config-dir", help="settings directory (default: the platform's config dir)")
    ap.add_argument("-v", "--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s.%(msecs)03d %(levelname).1s %(name)s: %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stdout)
    logging.getLogger("websockets").setLevel(logging.WARNING)

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setOrganizationName("")
    if args.config_dir:
        os.environ["LAPIN_CONFIG_DIR"] = args.config_dir

    from .config import Config, default_dir
    _record_crashes(default_dir())

    if args.earcon:
        import time
        import sounddevice as sd
        from .audio import make_earcon
        rate = 48000
        x = make_earcon(args.earcon, rate)
        sd.play(x, rate)
        time.sleep(len(x) / rate + 0.2)
        return 0
    if args.text or args.wav:
        app = QApplication(sys.argv[:1])
        cfg = Config()
        i18n.setup(cfg["language"])
        from .tools.common import setup_main_thread
        setup_main_thread()
        from . import cli
        return cli.run(args, cfg)

    from .single import SingleInstance

    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)
    command = "settings" if args.settings else "quit" if args.quit else "activate" if args.activate else "show"
    if SingleInstance.send(command):
        return 0                    # the running instance handles it
    if args.quit:
        return 0
    cfg = Config()
    i18n.setup(cfg["language"])
    from .app import DesktopApp
    desk = DesktopApp(app, cfg, headless=args.headless)
    if not desk.start():
        return 1
    if args.activate:
        desk.activate_later()
    elif args.settings:
        desk.show_settings()
    code = app.exec()
    desk.shutdown()
    return code


if __name__ == "__main__":
    sys.exit(main())
