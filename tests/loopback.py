"""Small loopback servers (127.0.0.1 only) that misbehave on purpose, for the
tests that drive real load engines. Nothing here binds a public address."""
import os
import socket
import struct
import threading
import time


class LoopbackServer:
    """Accepts on 127.0.0.1 and runs ``handler(server, conn)`` per connection."""

    def __init__(self, handler):
        self.handler = handler
        self.lock = threading.Lock()
        self.connections = 0
        self.heads = []
        self._conns = []
        self._closed = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(128)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True, name="loopback-accept").start()

    def _accept(self):
        while not self._closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with self.lock:
                self.connections += 1
                self._conns.append(conn)
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            self.handler(self, conn)
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def read_head(self, conn):
        """The next request head (through the blank line), or None at EOF."""
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(65536)
            if not chunk:
                return None
            data += chunk
        head = data[:data.index(b"\r\n\r\n") + 4]
        with self.lock:
            self.heads.append(head)
        return head

    def close(self):
        self._closed = True
        for action in (lambda: self.sock.shutdown(socket.SHUT_RDWR), self.sock.close):
            try:
                action()
            except OSError:
                pass
        with self.lock:
            conns, self._conns = self._conns, []
        for conn in conns:
            try:
                conn.close()
            except OSError:
                pass


def http_ok(server, conn):
    while server.read_head(conn) is not None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")


def http_redirect(location):
    def handler(server, conn):
        while server.read_head(conn) is not None:
            conn.sendall(b"HTTP/1.1 302 Found\r\nLocation: " + location.encode("ascii")
                         + b"\r\nContent-Length: 0\r\n\r\n")
    return handler


def http_truncated(server, conn):
    """Promises 100 bytes of body, sends 10, then closes."""
    if server.read_head(conn) is not None:
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n0123456789")


def http_slow_body(chunks, gap):
    """A chunked body of ``chunks`` one-byte pieces, one every ``gap`` seconds."""
    def handler(server, conn):
        while server.read_head(conn) is not None:
            conn.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            for _ in range(chunks):
                time.sleep(gap)
                conn.sendall(b"1\r\nx\r\n")
            conn.sendall(b"0\r\n\r\n")
    return handler


def silent(server, conn):
    """Reads whatever arrives and never answers, until the client goes away."""
    conn.settimeout(30)
    try:
        while conn.recv(65536):
            pass
    except OSError:
        pass


def tcp_reset_after_data(server, conn):
    """Reads the payload, then resets the connection (RST, not FIN)."""
    conn.settimeout(5)
    conn.recv(65536)
    linger = struct.pack("hh" if os.name == "nt" else "ii", 1, 0)
    conn.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, linger)
