"""应用层：菜单、三种对局模式、事件循环、悔棋/重开/投降协商。"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time

from . import ai as ai_mod
from . import discovery, net, protocol, render, session
from . import terminal as T
from .board import BLACK, WHITE, coord_name
from .session import other

MODES = {
    "host": "联机对战 · 主机",
    "client": "联机对战 · 加入",
    "local": "本地双人",
    "ai": "人机对战",
}

MENU_ITEMS = [
    ("1", "创建房间（作为主机）"),
    ("2", "加入房间（局域网）"),
    ("3", "本地双人对战"),
    ("4", "人机对战"),
    ("5", "退出"),
]


class App:
    def __init__(self, port: int = net.DEFAULT_PORT):
        self.port = port
        self.keys = T.KeyPump()
        self.peer = None
        self.mode = None
        self.my_color = None
        self.ai_color = None
        self.names = ("黑方", "白方")
        self.game = None
        self.cursor = (7, 7)
        self.notice = ""
        self.dirty = True
        self.leave = False
        self.pending = None
        self.ask = None
        self.ai_due = None
        self.last_ping = 0.0
        self.link_dead = False
        self.replay_done = False

    # ------------------------------------------------------------------ 启动

    def run(self) -> None:
        T.init_console()
        T.hide_cursor()
        self.keys.start()
        try:
            while True:
                choice = self.menu()
                if choice == "5":
                    break
                if choice == "1":
                    self.run_host()
                elif choice == "2":
                    self.run_client()
                elif choice == "3":
                    self.run_local()
                elif choice == "4":
                    self.run_ai()
                self.cleanup_net()
        except KeyboardInterrupt:
            pass
        finally:
            self.cleanup_net()
            T.show_cursor()
            T.clear()
            print("已退出五子棋。")

    # ------------------------------------------------------------------ 绘图

    def draw(self, lines) -> None:
        body = "".join(line + "\x1b[K\n" for line in lines)
        sys.stdout.write("\x1b[H" + body + "\x1b[J")
        sys.stdout.flush()

    def message(self, text: str) -> None:
        lines = [f"{T.C_TITLE} 提示{T.RESET}", ""]
        for chunk in str(text).split("\n"):
            lines.append("  " + chunk)
        lines += ["", f"{T.C_DIM} 按任意键继续{T.RESET}"]
        self.draw(lines)
        while True:
            key = self.keys.wait(0.5)
            if key:
                return

    def read_line(self, prompt: str, initial: str = ""):
        buf = list(initial)
        while True:
            self.draw([
                f"{T.C_TITLE} 输入{T.RESET}",
                "",
                f"  {prompt}{''.join(buf)}_",
                "",
                f"{T.C_DIM} 回车确认　Esc 取消　退格删除{T.RESET}",
            ])
            key = self.keys.wait(0.3)
            if not key:
                continue
            name = key[0]
            if name == "ENTER":
                return "".join(buf)
            if name in ("ESC", "CTRL_C"):
                return None
            if name == "BS":
                if buf:
                    buf.pop()
            elif name == "CHAR":
                ch = key[1]
                if ch.isprintable() and len(buf) < 24:
                    buf.append(ch)

    # ------------------------------------------------------------------ 菜单

    def menu(self):
        labels = [item[1] for item in MENU_ITEMS]
        sel = 0
        while True:
            lines = [
                f"{T.C_TITLE} 五子棋 · 控制台版{T.RESET}",
                "",
                f"{T.C_DIM} ↑↓ 选择，回车确认，也可直接按数字{T.RESET}",
                "",
            ]
            for index, label in enumerate(labels):
                active = index == sel
                style = T.C_HL if active else ""
                mark = ">" if active else " "
                lines.append(f"  {style}{mark} {MENU_ITEMS[index][0]}. {label}{T.RESET}")
            lines += [
                "",
                f"{T.C_DIM} 联机需两台机器在同一局域网，防火墙放行 TCP {self.port} 与 UDP {discovery.DISCOVERY_PORT}{T.RESET}",
                f"{T.C_DIM} 本机地址: {', '.join(net.local_ips())}{T.RESET}",
            ]
            self.draw(lines)
            key = self.keys.wait(0.25)
            if not key:
                continue
            name = key[0]
            if name == "UP":
                sel = (sel - 1) % len(labels)
            elif name == "DOWN":
                sel = (sel + 1) % len(labels)
            elif name == "ENTER":
                return MENU_ITEMS[sel][0]
            elif name == "CHAR":
                if key[1] in [item[0] for item in MENU_ITEMS]:
                    return key[1]
                if key[1].lower() == "q":
                    return "5"
            elif name in ("ESC", "CTRL_C"):
                return "5"

    # ------------------------------------------------------------------ 联机入口

    def run_host(self) -> None:
        name = self.read_line("你的名字（回车用默认）: ", "玩家A")
        if name is None:
            return
        name = name.strip() or "玩家A"

        try:
            listener = net.HostListener(self.port)
        except OSError as exc:
            self.message(f"端口 {self.port} 监听失败：{exc}\n可能已有另一个房间在运行。")
            return

        stop_event = threading.Event()
        discovery.start_beacon(f"{name} 的房间", self.port, stop_event)
        try:
            while self.peer is None:
                self.draw(self.wait_screen(name))
                key = self.keys.poll(0.2)
                if key and key[0] in ("ESC", "CTRL_C"):
                    return
                accepted = listener.accept()
                if accepted:
                    conn, addr = accepted
                    self.peer = net.Peer(conn, addr).start()
                    self.last_ping = time.time()
        finally:
            stop_event.set()
            listener.close()

        if not self.handshake(name, initiator=True):
            self.cleanup_net()
            return

        black, white = self.names
        self.mode = "host"
        self.my_color = BLACK
        self.game = session.Game(black_name=black, white_name=white)
        self.loop()

    def run_client(self) -> None:
        name = self.read_line("你的名字（回车用默认）: ", "玩家B")
        if name is None:
            return
        name = name.strip() or "玩家B"

        rooms = self.scan_screen()
        if rooms is None:
            return

        host_ip, port = None, self.port
        if rooms:
            room = self.choose_room(rooms)
            if room is None:
                return
            host_ip, port = room["ip"], room["port"]
        else:
            self.message("没有扫描到房间。\n请让主机先点「创建房间」，或手动填写主机 IP。")
            host_ip = self.read_line("主机 IP（例 192.168.1.10）: ", "")
            if not host_ip or not host_ip.strip():
                return
            host_ip = host_ip.strip()
            raw_port = self.read_line(f"端口（回车默认 {self.port}）: ", str(self.port))
            if raw_port is None:
                return
            try:
                port = int(raw_port.strip() or self.port)
            except ValueError:
                port = self.port

        self.draw([f"{T.C_TITLE} 正在连接 {host_ip}:{port} …{T.RESET}", ""])
        try:
            self.peer = net.connect(host_ip, port).start()
        except OSError as exc:
            self.message(str(exc))
            return
        self.last_ping = time.time()

        if not self.handshake(name, initiator=False):
            self.cleanup_net()
            return

        black, white = self.names
        self.mode = "client"
        self.my_color = BLACK if black == name else WHITE
        self.game = session.Game(black_name=black, white_name=white)
        self.loop()

    def handshake(self, my_name: str, initiator: bool) -> bool:
        self.draw([f"{T.C_TITLE} 正在与对手握手…{T.RESET}", ""])
        self.peer.send("hello", {"name": my_name, "ver": protocol.PROTOCOL_VERSION})
        data = self.wait_message("hello", timeout=8.0)
        if not data:
            self.message("握手失败：对方没有响应，可能已断开连接。")
            return False
        peer_name = str(data.get("name") or "对手")[:16]

        if initiator:
            black, white = my_name, peer_name
            self.peer.send("start", {"black": black, "white": white})
        else:
            info = self.wait_message("start", timeout=8.0)
            if not info:
                self.message("等待房主开局超时。")
                return False
            black = str(info.get("black") or "黑方")
            white = str(info.get("white") or "白方")

        self.names = (black, white)
        return True

    def wait_screen(self, name: str):
        lines = [
            f"{T.C_TITLE} 房间已创建，等待对手加入…{T.RESET}",
            "",
            f"  房间名: {name} 的房间",
            "",
            "  把下面的地址告诉对手，或让他在「加入房间」里自动扫描：",
            "",
        ]
        for ip in net.local_ips():
            lines.append(f"    {T.C_HL}{ip}:{self.port}{T.RESET}")
        lines += ["", f"{T.C_DIM} 正在广播房间信息…　按 Esc 取消{T.RESET}"]
        return lines

    def scan_screen(self):
        box = {}

        def work():
            box["rooms"] = discovery.scan_rooms(2.5)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        spin = "|/-\\"
        index = 0
        while thread.is_alive():
            self.draw([
                f"{T.C_TITLE} 正在扫描局域网内的房间{T.RESET}",
                "",
                f"  {spin[index % 4]}  请稍候…（Esc 取消）",
            ])
            index += 1
            key = self.keys.poll(0.12)
            if key and key[0] in ("ESC", "CTRL_C"):
                return None
        return box.get("rooms") or []

    def choose_room(self, rooms):
        sel = 0
        while True:
            lines = [f"{T.C_TITLE} 发现 {len(rooms)} 个房间{T.RESET}", ""]
            for index, room in enumerate(rooms):
                active = index == sel
                style = T.C_HL if active else ""
                mark = ">" if active else " "
                lines.append(
                    f"  {style}{mark} {index + 1}. {room['name']}　{room['ip']}:{room['port']}{T.RESET}"
                )
            lines += ["", f"{T.C_DIM} ↑↓ 选择，回车加入，Esc 返回{T.RESET}"]
            self.draw(lines)
            key = self.keys.wait(0.25)
            if not key:
                continue
            name = key[0]
            if name == "UP":
                sel = (sel - 1) % len(rooms)
            elif name == "DOWN":
                sel = (sel + 1) % len(rooms)
            elif name == "ENTER":
                return rooms[sel]
            elif name == "CHAR" and key[1].isdigit():
                index = int(key[1]) - 1
                if 0 <= index < len(rooms):
                    return rooms[index]
            elif name in ("ESC", "CTRL_C"):
                return None

    # ------------------------------------------------------------------ 单机入口

    def run_local(self) -> None:
        self.mode = "local"
        self.my_color = None
        self.ai_color = None
        self.game = session.Game(black_name="玩家1", white_name="玩家2")
        self.loop()

    def run_ai(self) -> None:
        self.mode = "ai"
        self.my_color = BLACK
        self.ai_color = WHITE
        self.game = session.Game(black_name="你", white_name="电脑")
        self.loop()

    # ------------------------------------------------------------------ 主循环

    def loop(self) -> None:
        self.leave = False
        self.pending = None
        self.ask = None
        self.notice = ""
        self.link_dead = False
        self.replay_done = False
        self.ai_due = None
        self.cursor = (self.game.size // 2, self.game.size // 2)
        self.dirty = True
        if self.mode == "ai" and self.game.turn == self.ai_color:
            self.ai_due = time.time() + 0.5

        while not self.leave:
            if self.dirty:
                self.draw(self.game_screen())
                self.dirty = False

            key = self.keys.poll(0.04)
            if key:
                self.on_key(key)

            if self.mode in ("host", "client") and self.peer:
                self.pump_net()

            if self.ai_due is not None and time.time() >= self.ai_due:
                self.ai_due = None
                self.ai_play()

        if self.mode in ("host", "client") and self.peer:
            self.peer.send("bye")
            time.sleep(0.05)

    # ------------------------------------------------------------------ 键盘

    def on_key(self, key) -> None:
        self.dirty = True
        name = key[0]

        if self.ask is not None:
            if name == "CHAR" and key[1].lower() in ("y", "n"):
                self.answer_ask(key[1].lower() == "y")
            elif name in ("ESC", "CTRL_C"):
                self.answer_ask(False)
            return

        if name == "UP":
            self.move_cursor(0, -1)
        elif name == "DOWN":
            self.move_cursor(0, 1)
        elif name == "LEFT":
            self.move_cursor(-1, 0)
        elif name == "RIGHT":
            self.move_cursor(1, 0)
        elif name == "ENTER":
            self.try_move()
        elif name == "ESC":
            self.ask = {"kind": "leave"}
            self.notice = "离开本局？Y 确认 / N 取消"
        elif name == "CTRL_C":
            self.ask = {"kind": "leave"}
            self.notice = "离开本局？Y 确认 / N 取消"
        elif name == "CHAR":
            ch = key[1].lower()
            if ch == " ":
                self.try_move()
            elif ch == "u":
                self.on_undo()
            elif ch == "r":
                self.on_restart()
            elif ch == "q":
                self.on_resign()

    def move_cursor(self, dx: int, dy: int) -> None:
        x, y = self.cursor
        x = max(0, min(self.game.size - 1, x + dx))
        y = max(0, min(self.game.size - 1, y + dy))
        self.cursor = (x, y)

    def try_move(self) -> None:
        game = self.game
        if self.link_dead:
            self.notice = "连接已断开，按 Esc 返回菜单"
            return
        if game.status != "playing":
            self.notice = "对局已结束，按 R 再来一局"
            return
        if self.pending:
            self.notice = "请先处理当前请求"
            return
        if self.mode in ("host", "client", "ai") and game.turn != self.my_color:
            self.notice = "还没轮到你落子"
            return

        x, y = self.cursor
        ok, why = game.play(x, y)
        if not ok:
            self.notice = why
            return

        if self.mode in ("host", "client"):
            self.peer.send("move", {"x": x, "y": y})
        self.notice = f"落子 {coord_name(x, y)}"
        self.check_over()

        if self.mode == "ai" and game.status == "playing" and game.turn == self.ai_color:
            self.ai_due = time.time() + 0.45

    def on_undo(self) -> None:
        game = self.game
        if self.link_dead:
            self.notice = "连接已断开，无法悔棋"
            return
        if self.pending:
            self.notice = "上一个请求还在等待回应"
            return
        if game.status != "playing":
            self.notice = "对局已结束，无法悔棋"
            return

        if self.mode in ("host", "client"):
            ok, why = game.can_undo(self.my_color)
            if not ok:
                self.notice = why
                return
            target = game.undo_target_for(self.my_color)
            self.peer.send("undo_req", {"target": target})
            self.pending = {"kind": "undo_sent", "target": target}
            self.notice = "已发送悔棋请求，等待对方回应…"
            return

        if not game.board.moves:
            self.notice = "还没有落子"
            return
        requester = self.my_color if self.mode == "ai" else game.board.moves[-1][2]
        ok, why = game.can_undo(requester)
        if not ok:
            self.notice = why
            return
        target = game.undo_target_for(requester)
        self.ask = {"kind": "undo_local", "req": requester, "target": target}
        self.notice = "确认悔棋？Y 同意 / N 取消"

    def on_restart(self) -> None:
        if self.link_dead:
            self.notice = "连接已断开，按 Esc 返回菜单"
            return
        if self.pending:
            self.notice = "上一个请求还在等待回应"
            return
        if self.mode in ("host", "client"):
            self.peer.send("restart_req")
            self.pending = {"kind": "restart_sent"}
            self.notice = "已发送重开请求，等待对方回应…"
            return
        self.ask = {"kind": "restart_local"}
        self.notice = "确认重新开始？Y 同意 / N 取消"

    def on_resign(self) -> None:
        if self.link_dead:
            self.notice = "连接已断开"
            return
        if self.game.status != "playing":
            self.notice = "对局已结束"
            return
        self.ask = {"kind": "resign"}
        self.notice = "确认投降？Y 确认 / N 取消"

    def answer_ask(self, yes: bool) -> None:
        ask, self.ask = self.ask, None
        self.dirty = True
        if not ask:
            return
        kind = ask["kind"]
        game = self.game

        if kind == "leave":
            if yes:
                self.leave = True
            else:
                self.notice = ""
            return

        if kind == "resign":
            if not yes:
                self.notice = "已取消投降"
                return
            color = game.turn if self.mode == "local" else self.my_color
            if self.mode in ("host", "client"):
                self.peer.send("resign", {"color": color})
            game.resign(color)
            self.notice = "你已投降"
            self.check_over()
            return

        if kind == "undo_local":
            if not yes:
                self.notice = "已取消悔棋"
                return
            game.undo_used[ask["req"]] += 1
            game.apply_undo(ask["target"])
            self.notice = "悔棋成功"
            return

        if kind == "undo_recv":
            peer_color = ask["req"]
            self.pending = None
            if yes:
                game.undo_used[peer_color] += 1
                game.apply_undo(ask["target"])
                self.peer.send("undo_ack", {"accept": True, "target": ask["target"]})
                self.notice = "已同意对方悔棋"
            else:
                self.peer.send("undo_ack", {"accept": False, "reason": "对方拒绝"})
                self.notice = "已拒绝对方悔棋"
            return

        if kind == "restart_local":
            if not yes:
                self.notice = "已取消重开"
                return
            self.apply_restart()
            self.notice = "新的一局开始"
            return

        if kind == "restart_recv":
            self.pending = None
            if yes:
                self.apply_restart()
                self.peer.send("restart_ack", {"accept": True})
                self.notice = "已同意重开，新的一局开始"
            else:
                self.peer.send("restart_ack", {"accept": False})
                self.notice = "已拒绝重开"
            return

    def apply_restart(self) -> None:
        swapped = self.game.restart(swap=True)
        if swapped and self.mode in ("host", "client", "ai"):
            self.my_color = other(self.my_color)
        if self.mode == "ai":
            self.ai_color = other(self.ai_color)
        self.cursor = (self.game.size // 2, self.game.size // 2)
        self.pending = None
        self.ask = None
        self.replay_done = False
        self.ai_due = None
        self.dirty = True
        if self.mode == "ai" and self.game.turn == self.ai_color:
            self.ai_due = time.time() + 0.45

    # ------------------------------------------------------------------ 网络收发

    def pump_net(self) -> None:
        if not self.peer:
            return
        handled = False
        while True:
            try:
                msg_type, payload, _game, _seq = self.peer.inbox.get_nowait()
            except queue.Empty:
                break
            self.handle_msg(msg_type, payload)
            handled = True
        if handled:
            self.dirty = True
        self.heartbeat()

    def heartbeat(self) -> None:
        if not self.peer:
            return
        if not self.peer.connected:
            self.link_lost()
            return
        now = time.time()
        if now - self.last_ping >= 1.0:
            self.last_ping = now
            self.peer.send("ping")
        if now - self.peer.last_rx > 8.0:
            self.link_lost()

    def handle_msg(self, msg_type: str, payload: dict) -> None:
        game = self.game
        if msg_type == "__closed__":
            self.link_lost()
            return
        if msg_type == "__bad__":
            self.notice = f"收到无法解析的数据：{payload.get('msg')}"
            return
        if msg_type == "ping":
            self.peer.send("pong")
            return
        if msg_type == "pong":
            return
        if msg_type == "error":
            self.notice = f"对方提示：{payload.get('msg')}"
            return
        if msg_type == "bye":
            self.notice = "对手离开了对局"
            if game.status == "playing":
                game.mark_disconnected()
                self.check_over()
            self.link_dead = True
            return

        if msg_type == "move":
            if game.status != "playing":
                self.send_err("对局已结束")
                return
            if game.turn == self.my_color:
                self.send_err("还没轮到你落子")
                return
            try:
                x, y = int(payload.get("x")), int(payload.get("y"))
            except (TypeError, ValueError):
                self.send_err("坐标格式错误")
                return
            ok, why = game.play(x, y)
            if not ok:
                self.send_err(why)
                self.notice = f"拒绝对方落子：{why}"
                return
            self.notice = f"对方落子 {coord_name(x, y)}"
            self.check_over()
            return

        if msg_type == "undo_req":
            peer_color = other(self.my_color)
            if self.pending:
                self.peer.send("undo_ack", {"accept": False, "reason": "对方有请求在处理"})
                return
            ok, why = game.can_undo(peer_color)
            if not ok:
                self.peer.send("undo_ack", {"accept": False, "reason": why})
                self.notice = f"对方请求悔棋，自动拒绝（{why}）"
                return
            target = game.undo_target_for(peer_color)
            self.pending = {"kind": "undo_recv", "req": peer_color, "target": target}
            self.ask = {"kind": "undo_recv", "req": peer_color, "target": target}
            self.notice = "对方请求悔棋：Y 同意 / N 拒绝"
            return

        if msg_type == "undo_ack":
            if not self.pending or self.pending.get("kind") != "undo_sent":
                return
            pending, self.pending = self.pending, None
            if not payload.get("accept"):
                self.notice = f"对方拒绝悔棋：{payload.get('reason') or '未说明原因'}"
                return
            try:
                target = int(payload.get("target", pending["target"]))
            except (TypeError, ValueError):
                target = pending["target"]
            local = game.undo_target_for(self.my_color)
            if local is not None:
                target = local
            game.undo_used[self.my_color] += 1
            game.apply_undo(target)
            self.notice = "对方同意悔棋"
            return

        if msg_type == "resign":
            peer_color = other(self.my_color)
            if game.status == "playing":
                game.resign(peer_color)
                self.notice = "对方投降了"
                self.check_over()
            return

        if msg_type == "restart_req":
            if self.pending:
                self.peer.send("restart_ack", {"accept": False})
                return
            self.pending = {"kind": "restart_recv"}
            self.ask = {"kind": "restart_recv"}
            self.notice = "对方请求重新开始：Y 同意 / N 拒绝"
            return

        if msg_type == "restart_ack":
            if not self.pending or self.pending.get("kind") != "restart_sent":
                return
            self.pending = None
            if payload.get("accept"):
                self.apply_restart()
                self.notice = "对方同意重开，新的一局开始"
            else:
                self.notice = "对方拒绝了重开请求"
            return

    def send_err(self, text: str) -> None:
        if self.peer:
            self.peer.send("error", {"msg": text})

    def wait_message(self, want: str, timeout: float = 8.0):
        if not self.peer:
            return None
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg_type, payload, _game, _seq = self.peer.inbox.get(timeout=0.1)
            except queue.Empty:
                if not self.peer.connected:
                    return None
                key = self.keys.poll(0)
                if key and key[0] in ("ESC", "CTRL_C"):
                    return None
                continue
            if msg_type == "__closed__":
                return None
            if msg_type == want:
                return payload
        return None

    def link_lost(self) -> None:
        if self.link_dead:
            return
        self.link_dead = True
        self.pending = None
        self.ask = None
        self.notice = "与对手的连接已断开"
        if self.game and self.game.status == "playing":
            self.game.mark_disconnected()
            self.check_over()
        self.dirty = True

    def cleanup_net(self) -> None:
        if self.peer:
            try:
                self.peer.send("bye")
            except Exception:
                pass
            self.peer.close()
            self.peer = None
        self.pending = None
        self.ask = None
        self.link_dead = False

    # ------------------------------------------------------------------ AI

    def ai_play(self) -> None:
        game = self.game
        if game.status != "playing" or game.turn != self.ai_color:
            return
        move = ai_mod.choose_move(game.board, self.ai_color)
        if not move:
            self.notice = "电脑没有可落子的位置"
            return
        x, y = move
        ok, why = game.play(x, y)
        if not ok:
            self.notice = f"电脑落子失败：{why}"
            return
        self.notice = f"电脑落子 {coord_name(x, y)}"
        self.check_over()

    # ------------------------------------------------------------------ 结束与画面

    def check_over(self) -> None:
        game = self.game
        self.dirty = True
        if game.status != "over" or self.replay_done:
            return
        self.replay_done = True
        try:
            path = session.export_replay(game)
            self.notice = f"{game.result_text()}　回放：{os.path.basename(path)}"
        except OSError as exc:
            self.notice = f"{game.result_text()}　回放保存失败：{exc}"

    def game_screen(self):
        game = self.game
        black_badge = render.stone_badge(BLACK)
        white_badge = render.stone_badge(WHITE)
        lines = [
            f"{T.C_TITLE} 五子棋 · {MODES[self.mode]}{T.RESET}",
            f" {black_badge} {game.players[BLACK]}    {white_badge} {game.players[WHITE]}"
            f"    比分 {game.scores[BLACK]} : {game.scores[WHITE]}    第 {game.game_no} 局",
            "",
        ]
        lines += render.board_lines(game, self.cursor, game.highlight)
        lines.append(f"{T.C_DIM} " + "-" * 32 + T.RESET)
        lines.append(" " + self.status_line())
        lines.append(" " + self.action_line())
        lines.append(" " + self.notice)
        lines.append(" " + self.hint_line())
        return lines

    def status_line(self) -> str:
        game = self.game
        if game.status == "over":
            return f"{T.C_OK}{game.result_text()}{T.RESET}"
        badge = render.stone_badge(game.turn)
        side = "黑方" if game.turn == BLACK else "白方"
        tag = ""
        if self.mode in ("host", "client", "ai"):
            if game.turn == self.my_color:
                tag = "（你）"
            else:
                tag = "（电脑）" if self.mode == "ai" else "（对手）"
        return f"轮到 {badge} {side}{tag}    第 {game.move_count + 1} 手"

    def action_line(self) -> str:
        game = self.game
        if game.status == "over":
            return f"{T.C_HL}R{T.RESET} 再来一局    {T.C_HL}Esc{T.RESET} 返回菜单"
        color = game.turn if self.mode == "local" else self.my_color
        left = game.undo_limit - game.undo_used.get(color, 0) if color else game.undo_limit
        return (
            f"方向键移动  {T.C_HL}空格{T.RESET}落子  "
            f"{T.C_HL}U{T.RESET}悔棋({left})  {T.C_HL}R{T.RESET}重开  "
            f"{T.C_HL}Q{T.RESET}投降  {T.C_HL}Esc{T.RESET}离开"
        )

    def hint_line(self) -> str:
        x, y = self.cursor
        last = self.game.last_move
        last_text = f"最后一手 {coord_name(last[0], last[1])}" if last else "尚无落子"
        return f"{T.C_DIM}光标 {coord_name(x, y)}    {last_text}{T.RESET}"
