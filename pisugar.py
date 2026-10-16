"""Small helpers for talking to pisugar-server over its TCP interface."""

import logging
import socket

from config import PISUGAR_HOST, PISUGAR_PORT

logger = logging.getLogger(__name__)


def query(command, timeout=1.0):
    """Send one command (e.g. 'get battery') and return the value after the colon."""
    with socket.create_connection((PISUGAR_HOST, PISUGAR_PORT), timeout=timeout) as s:
        s.sendall(command.encode() + b"\n")
        reply = b""
        # The server may interleave button events ("single", ...) on this
        # connection, so read until we see the line for our key.
        key = command.split()[-1]
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            reply += chunk
            for line in reply.decode(errors="ignore").splitlines():
                if line.startswith(key + ":"):
                    return line.split(":", 1)[1].strip()
    raise ValueError(f"No '{key}' in reply: {reply!r}")


def get_battery_percentage():
    """Battery level 0-100, or None if pisugar-server isn't reachable."""
    try:
        return max(0, min(100, round(float(query("get battery")))))
    except Exception as e:
        logger.debug(f"Battery query failed: {e}")
        return None
