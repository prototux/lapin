"""Single instance: the first instance listens on a local socket; running the
app again (e.g. `--activate` bound to a desktop shortcut on Wayland) sends it
a command instead of starting a second copy."""

import getpass
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from . import APP_NAME

log = logging.getLogger("single")


def server_name():
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "%s-%s" % (APP_NAME, "".join(c for c in user if c.isalnum()) or "user")


class SingleInstance(QObject):
    command = Signal(str)       # activate | settings | quit | show

    def __init__(self):
        super().__init__()
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        self.server.newConnection.connect(self._accept)

    @staticmethod
    def send(command, timeout_ms=600):
        """True if a running instance got the command."""
        sock = QLocalSocket()
        sock.connectToServer(server_name())
        if not sock.waitForConnected(timeout_ms):
            return False
        sock.write((command + "\n").encode())
        sock.flush()
        sock.waitForBytesWritten(timeout_ms)
        sock.disconnectFromServer()
        return True

    def listen(self):
        name = server_name()
        if not self.server.listen(name):
            # a stale socket left by a crash (send() already found nobody there)
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                log.warning("single instance socket: %s", self.server.errorString())
                return False
        return True

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        while sock.canReadLine():
            cmd = bytes(sock.readLine()).decode(errors="replace").strip()
            if cmd:
                log.info("command from another instance: %s", cmd)
                self.command.emit(cmd)
