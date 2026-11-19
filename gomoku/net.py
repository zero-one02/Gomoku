"""TCP 传输：主机监听、加入连接、读线程、心跳收发。"""

from __future__ import annotations

import queue
import socket
import threading
import time

from . import protocol

DEFAULT_PORT = 9527


def local_ips() -> list:
    """尽量列出本机可用于局域网访问的 IPv4。"""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ips.append(probe.getsockname()[0])
        except OSError:
            pass
        finally:
            probe.close()
    return ips or ["127.0.0.1"]


class HostListener:
    """监听端口等待对手接入，accept 可被打断。"""

    def __init__(self, port: int = DEFAULT_PORT):
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", port))
        self.sock.listen(1)
        self.sock.settimeout(0.25)

    def accept(self):
        """返回 (conn, addr)；暂无连接返回 None。"""
        try:
            conn, addr = self.sock.accept()
            conn.settimeout(None)
            return conn, addr
        except socket.timeout:
            return None
        except OSError:
            return None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class Peer:
    """一条已建立的对等连接，读线程把消息塞进 inbox。"""

    def __init__(self, sock: socket.socket, addr=None):
        self.sock = sock
        self.addr = addr
        self.inbox: "queue.Queue" = queue.Queue()
        self.connected = True
        self.last_rx = time.time()
        self.bytes_in = 0
        self.bytes_out = 0
        self._lock = threading.Lock()
        self._stop = False
        self._thread = threading.Thread(target=self._read_loop, name="peer-reader", daemon=True)

    def start(self) -> "Peer":
        self._thread.start()
        return self

    def _read_loop(self) -> None:
        try:
            self.sock.settimeout(0.5)
        except OSError:
            pass
        buf = b""
        while not self._stop:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            if not chunk:
                break
            self.last_rx = time.time()
            self.bytes_in += len(chunk)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg_type, payload, game, seq = protocol.decode(line)
                except protocol.ProtocolError as exc:
                    self.inbox.put(("__bad__", {"msg": str(exc)}, None, None))
                    continue
                self.inbox.put((msg_type, payload, game, seq))
        self.connected = False
        self.inbox.put(("__closed__", {}, None, None))

    def send(self, msg_type: str, payload: dict = None, game: int = 1, seq: int = 0) -> bool:
        with self._lock:
            if not self.connected:
                return False
            data = protocol.encode(msg_type, payload, game, seq)
            try:
                self.sock.sendall(data)
                self.bytes_out += len(data)
                return True
            except OSError:
                self.connected = False
                return False

    def close(self) -> None:
        self._stop = True
        self.connected = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


def connect(host: str, port: int = DEFAULT_PORT, timeout: float = 6.0):
    """连接对手；成功返回 Peer，失败抛出 OSError 并带中文说明。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    except socket.timeout:
        sock.close()
        raise OSError("连接超时：请确认对方已创建房间，且防火墙放行了 TCP 端口")
    except OSError as exc:
        sock.close()
        raise OSError(f"无法连接到 {host}:{port}（{exc}）") from exc
    sock.settimeout(None)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return Peer(sock, (host, port))
