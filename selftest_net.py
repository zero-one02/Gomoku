"""联机流程自检：用真实 TCP 连接驱动两个 App 实例，验证落子同步、
悔棋协商、重开换色与投降结算。不进入交互界面。

运行: python selftest_net.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gomoku import net, session  # noqa: E402
from gomoku.app import App  # noqa: E402
from gomoku.board import BLACK, WHITE  # noqa: E402
from gomoku.session import Game  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if detail and not ok else ""))


class FakeKeys:
    def poll(self, timeout: float = 0.0):
        return None

    wait = poll


def settle(*apps, seconds: float = 0.4) -> None:
    end = time.time() + seconds
    while time.time() < end:
        for app in apps:
            app.pump_net()
        time.sleep(0.01)


def boards_equal(a: App, b: App) -> bool:
    return a.game.board.grid == b.game.board.grid and a.game.turn == b.game.turn


def main() -> int:
    for app_cls_attr in ("draw", "message"):
        setattr(App, app_cls_attr, lambda self, *args, **kwargs: None)

    tmpdir = tempfile.mkdtemp(prefix="gomoku_replay_")
    session.REPLAY_DIR = tmpdir

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    client_sock = socket.create_connection(("127.0.0.1", port), timeout=3)
    conn, addr = server.accept()

    host_app = App(port=port)
    join_app = App(port=port)
    host_app.keys = FakeKeys()
    join_app.keys = FakeKeys()
    host_app.link_dead = join_app.link_dead = False
    host_app.replay_done = join_app.replay_done = False
    host_app.last_ping = join_app.last_ping = time.time()

    host_app.peer = net.Peer(conn, addr).start()
    join_app.peer = net.Peer(client_sock, ("127.0.0.1", port)).start()

    host_app.mode = "host"
    join_app.mode = "client"
    host_app.my_color = BLACK
    join_app.my_color = WHITE
    host_app.game = Game("甲", "乙")
    join_app.game = Game("甲", "乙")

    try:
        # 1. 黑方落子同步
        host_app.cursor = (7, 7)
        host_app.try_move()
        settle(host_app, join_app)
        check("落子同步到对手", join_app.game.move_count == 1 and join_app.game.board.get(7, 7) == BLACK)
        check("落子后轮次一致", boards_equal(host_app, join_app))

        # 2. 白方落子同步
        join_app.cursor = (8, 8)
        join_app.try_move()
        settle(host_app, join_app)
        check("白方落子同步", host_app.game.move_count == 2 and host_app.game.turn == BLACK)

        # 3. 非回合落子被拒绝
        join_app.cursor = (5, 5)
        join_app.try_move()
        settle(host_app, join_app)
        check("非回合落子被本地拦截", join_app.game.move_count == 2)

        # 4. 悔棋请求 → 对方同意
        host_app.on_undo()
        settle(host_app, join_app)
        check("对方收到悔棋请求", join_app.ask is not None and join_app.ask["kind"] == "undo_recv")
        join_app.answer_ask(True)
        settle(host_app, join_app)
        check("悔棋后手数一致", host_app.game.move_count == join_app.game.move_count == 0)
        check("悔棋后是我方回合", host_app.game.turn == BLACK and join_app.game.turn == BLACK)
        check("悔棋计数同步", host_app.game.undo_used[BLACK] == join_app.game.undo_used[BLACK] == 1)

        # 5. 悔棋请求 → 对方拒绝
        host_app.cursor = (6, 6)
        host_app.try_move()
        settle(host_app, join_app)
        host_app.on_undo()
        settle(host_app, join_app)
        join_app.answer_ask(False)
        settle(host_app, join_app)
        check("拒绝悔棋后棋盘不变", host_app.game.move_count == 1)
        check("拒绝后不扣次数", host_app.game.undo_used[BLACK] == 1)

        # 6. 重开并交换执色
        host_app.on_restart()
        settle(host_app, join_app)
        check("对方收到重开请求", join_app.ask is not None and join_app.ask["kind"] == "restart_recv")
        join_app.answer_ask(True)
        settle(host_app, join_app)
        check("重开后棋盘清空", host_app.game.move_count == 0 and join_app.game.move_count == 0)
        check("重开交换执色", host_app.my_color == WHITE and join_app.my_color == BLACK)
        check("重开后局号递增", host_app.game.game_no == join_app.game.game_no == 2)
        check("重开后仍同步", boards_equal(host_app, join_app))

        # 7. 投降结算
        host_app.on_resign()
        host_app.answer_ask(True)
        settle(host_app, join_app)
        check("投降后对手获胜", host_app.game.winner == BLACK and join_app.game.winner == BLACK)
        check("投降后比分同步", host_app.game.scores == join_app.game.scores)
        check("投降后回放已生成", host_app.replay_done and join_app.replay_done)
        check("回放文件落盘", len(os.listdir(tmpdir)) >= 1)

        # 8. 断线检测
        host_app.peer.close()
        settle(join_app, seconds=0.6)
        check("断线被识别", join_app.link_dead and join_app.game.status == "over")
    finally:
        for app in (host_app, join_app):
            if app.peer:
                app.peer.close()

    total, passed = len(RESULTS), sum(RESULTS)
    print(f"\n{passed}/{total} 项通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
