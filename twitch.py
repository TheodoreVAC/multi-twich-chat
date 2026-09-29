from __future__ import annotations

import socket
import ssl
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

from config import IRC_HOST, IRC_PORT, MAX_MESSAGE_LENGTH
from models import Account


class SignalBridge(QObject):
    message_received = Signal(str, str, str, str, str)
    connection_status = Signal(str, str)
    connection_error = Signal(str, str)
    log_message = Signal(str)


class TwitchIRCClient:
    def __init__(
        self,
        account: Account,
        channel: str,
        on_message: Callable[[str, str, str, str, str], None],
        on_status: Callable[[str, str], None],
        on_error: Callable[[str, str], None],
    ) -> None:
        self.account = account
        self.channel = channel.lstrip("#").strip().lower()
        self.on_message = on_message
        self.on_status = on_status
        self.on_error = on_error
        self._thread: Optional[threading.Thread] = None
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._socket_lock = threading.Lock()

    @property
    def username(self) -> str:
        return self.account.username

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"twitch-{self.username}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._close_socket()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)

    def send_message(self, message: str) -> bool:
        clean = " ".join(message.replace("\r", " ").replace("\n", " ").split())
        if not clean:
            return False
        if len(clean) > MAX_MESSAGE_LENGTH:
            raise ValueError(f"Message is longer than {MAX_MESSAGE_LENGTH} characters")
        try:
            with self._send_lock:
                sock = self._get_socket()
                if sock is None:
                    return False
                sock.sendall(f"PRIVMSG #{self.channel} :{clean}\r\n".encode("utf-8"))
            return True
        except OSError as exc:
            self.on_error(self.username, f"Send failed: {exc}")
            return False

    def _get_socket(self) -> Optional[socket.socket]:
        with self._socket_lock:
            return self._sock

    def _set_socket(self, sock: Optional[socket.socket]) -> None:
        with self._socket_lock:
            self._sock = sock

    def _close_socket(self) -> None:
        sock = self._get_socket()
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
        self._set_socket(None)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._connect_once()
            except Exception as exc:
                if not self._stop.is_set():
                    self.on_status(self.username, "ERROR")
                    self.on_error(self.username, str(exc))
            finally:
                self._close_socket()

            if not self._stop.is_set():
                self.on_status(self.username, "RECONNECTING")
                self._stop.wait(3.0)

        self.on_status(self.username, "OFFLINE")

    def _connect_once(self) -> None:
        self.on_status(self.username, "CHECKING")
        raw_sock = socket.create_connection((IRC_HOST, IRC_PORT), timeout=15)
        raw_sock.settimeout(90)
        context = ssl.create_default_context()
        sock = context.wrap_socket(raw_sock, server_hostname=IRC_HOST)
        self._set_socket(sock)

        token = self.account.token.strip()
        if token.lower().startswith("oauth:"):
            token = token[6:]

        self._send_raw("CAP REQ :twitch.tv/membership twitch.tv/tags twitch.tv/commands")
        self._send_raw(f"PASS oauth:{token}")
        self._send_raw(f"NICK {self.username}")
        self._send_raw(f"JOIN #{self.channel}")

        buffer = ""
        while not self._stop.is_set():
            try:
                chunk = sock.recv(8192)
            except socket.timeout:
                self._send_raw("PING :keepalive")
                continue

            if not chunk:
                raise ConnectionError("Twitch closed the connection")

            buffer += chunk.decode("utf-8", errors="replace")
            while "\r\n" in buffer:
                line, buffer = buffer.split("\r\n", 1)
                if line:
                    self._handle_line(line)

        self._close_socket()

    def _send_raw(self, line: str) -> None:
        sock = self._get_socket()
        if sock is None:
            raise ConnectionError("Socket is not connected")
        with self._send_lock:
            sock.sendall((line + "\r\n").encode("utf-8"))

    def _handle_line(self, line: str) -> None:
        if line.startswith("PING "):
            self._send_raw("PONG " + line[5:])
            return

        if "NOTICE" in line and "Login authentication failed" in line:
            raise ConnectionError("Twitch rejected the OAuth token")

        tags, rest = self._split_tags(line)
        prefix, command, params = self._parse_irc_line(rest)

        if command == "001":
            self.on_status(self.username, "CONNECTED")
            return

        if command == "PRIVMSG" and len(params) >= 2:
            target = params[0].lstrip("#").lower()
            message = params[1]
            sender = tags.get("display-name") or tags.get("login") or self._prefix_user(prefix)
            color = tags.get("color", "").strip()
            self.on_message(sender, message, target, tags.get("id", ""), color)

    @staticmethod
    def _split_tags(line: str) -> tuple[dict[str, str], str]:
        if not line.startswith("@"):
            return {}, line
        tag_text, rest = line.split(" ", 1)
        tags: dict[str, str] = {}
        for item in tag_text[1:].split(";"):
            key, _, value = item.partition("=")
            value = (
                value.replace("\\s", " ")
                .replace("\\:", ";")
                .replace("\\r", "\r")
                .replace("\\n", "\n")
                .replace("\\\\", "\\")
            )
            tags[key] = value
        return tags, rest

    @staticmethod
    def _parse_irc_line(line: str) -> tuple[str, str, list[str]]:
        prefix = ""
        if line.startswith(":"):
            prefix, line = line[1:].split(" ", 1)
        if " :" in line:
            before, trailing = line.split(" :", 1)
            parts = before.split()
            return prefix, parts[0], parts[1:] + [trailing]
        parts = line.split()
        return prefix, parts[0], parts[1:]

    @staticmethod
    def _prefix_user(prefix: str) -> str:
        return prefix.split("!", 1)[0] if "!" in prefix else prefix
