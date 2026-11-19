"""线协议：换行分隔的 JSON。

一条消息形如：{"t":"move","d":{"x":7,"y":7},"g":1,"s":12}
t=类型 d=消息体 g=局号 s=序号
"""

from __future__ import annotations

import json

PROTOCOL_VERSION = 1


class ProtocolError(Exception):
    pass


def encode(msg_type: str, payload: dict = None, game: int = 1, seq: int = 0) -> bytes:
    body = {
        "t": msg_type,
        "d": payload or {},
        "g": game,
        "s": seq,
    }
    text = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return (text + "\n").encode("utf-8")


def decode(line: bytes):
    try:
        obj = json.loads(line.decode("utf-8"))
    except Exception as exc:
        raise ProtocolError(f"无法解析 JSON: {exc}") from exc
    if not isinstance(obj, dict) or "t" not in obj:
        raise ProtocolError("消息缺少类型字段")
    payload = obj.get("d") or {}
    if not isinstance(payload, dict):
        raise ProtocolError("消息体必须是对象")
    msg_type = obj.get("t")
    if not isinstance(msg_type, str):
        raise ProtocolError("类型字段必须是字符串")
    return msg_type, payload, obj.get("g"), obj.get("s")


# 消息类型一览（也用于文档与自检）
CLIENT_MSG = {
    "hello", "start", "move", "undo_req", "undo_ack",
    "resign", "restart_req", "restart_ack", "ping", "pong", "bye", "error",
}
