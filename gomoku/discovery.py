"""局域网房间发现：主机用 UDP 广播信标，加入方扫描后直接选房间。"""

from __future__ import annotations

import json
import socket
import threading
import time

MAGIC = "GOMOKU-ROOM/1"
DISCOVERY_PORT = 9528
BROADCAST = "255.255.255.255"


def start_beacon(room_name: str, tcp_port: int, stop_event: threading.Event):
    """后台每秒广播一次房间信息。"""

    def run():
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps(
            {"magic": MAGIC, "name": room_name, "port": tcp_port},
            ensure_ascii=False,
        ).encode("utf-8")
        while not stop_event.is_set():
            try:
                sock.sendto(payload, (BROADCAST, DISCOVERY_PORT))
            except OSError:
                pass
            stop_event.wait(1.0)
        sock.close()

    thread = threading.Thread(target=run, name="beacon", daemon=True)
    thread.start()
    return thread


def scan_rooms(timeout: float = 2.5) -> list:
    """扫一轮局域网，返回 [{'name','ip','port'}]，按 IP 去重。"""
    rooms = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("", DISCOVERY_PORT))
    except OSError:
        sock.close()
        return []
    sock.settimeout(0.2)
    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                info = json.loads(data.decode("utf-8"))
            except Exception:
                continue
            if not isinstance(info, dict) or info.get("magic") != MAGIC:
                continue
            rooms[addr[0]] = {
                "name": str(info.get("name") or "未命名房间"),
                "ip": addr[0],
                "port": int(info.get("port") or 9527),
            }
    finally:
        sock.close()
    return list(rooms.values())
