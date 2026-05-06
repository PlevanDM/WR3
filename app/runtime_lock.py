from __future__ import annotations

import os
import socket
from pathlib import Path


class RuntimeLock:
    """Simple single-instance lock using a pid file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.fd: int | None = None
        self.sock: socket.socket | None = None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def acquire(self) -> bool:
        # Machine-wide mutex by TCP bind (independent of current directory).
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
            self.sock.bind(("127.0.0.1", 45991))
            self.sock.listen(1)
        except OSError:
            try:
                if self.sock is not None:
                    self.sock.close()
            except Exception:
                pass
            self.sock = None
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            self.fd = os.open(str(self.path), flags)
        except FileExistsError:
            try:
                old = self.path.read_text(encoding="utf-8").strip()
                old_pid = int(old or "0")
            except Exception:
                old_pid = 0
            if self._pid_alive(old_pid):
                return False
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                return False
            self.fd = os.open(str(self.path), flags)
        if self.fd is None:
            return False
        os.write(self.fd, str(os.getpid()).encode("utf-8"))
        os.fsync(self.fd)
        return True

    def release(self) -> None:
        try:
            if self.sock is not None:
                self.sock.close()
        except Exception:
            pass
        self.sock = None
        try:
            if self.fd is not None:
                os.close(self.fd)
        except Exception:
            pass
        self.fd = None
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
