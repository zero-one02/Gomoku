"""自检脚本：不进入交互界面，验证规则、悔棋一致性、AI、协议与传输。

运行: python selftest.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gomoku import ai, net, protocol, session  # noqa: E402
from gomoku.board import BLACK, EMPTY, WHITE, Board, parse_coord  # noqa: E402
from gomoku.session import Game, export_replay  # noqa: E402

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(ok)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f"  ({detail})" if detail and not ok else ""))


def test_win_lines() -> None:
    for label, steps in [
        ("横向五连", [(x, 7, BLACK) for x in range(3, 8)]),
        ("纵向五连", [(7, y, BLACK) for y in range(3, 8)]),
        ("主对角五连", [(i, i, BLACK) for i in range(3, 8)]),
        ("副对角五连", [(i, 10 - i, BLACK) for i in range(3, 8)]),
    ]:
        board = Board()
        line = None
        for x, y, color in steps:
            board.place(x, y, color)
            line = board.line_at(x, y)
        check(label, bool(line) and len(line) >= 5)

    board = Board()
    for x in range(4):
        board.place(x, 0, BLACK)
    check("四连不算赢", board.line_at(3, 0) is None)


def test_rules() -> None:
    game = Game("A", "B")
    ok, _ = game.play(7, 7)
    check("黑先落子成功", ok and game.turn == WHITE)
    ok, why = game.play(7, 7)
    check("重复落子被拒", not ok, why)
    ok, _ = game.play(8, 7)
    check("白方落子成功", ok and game.turn == BLACK)
    ok, _ = game.play(99, 99)
    check("越界被拒", not ok)

    game = Game("A", "B")
    for x in range(5):
        game.play(x, 0)
        if game.status == "playing":
            game.play(x, 1)
    check("五连判胜", game.status == "over" and game.winner == BLACK)
    check("胜方计分", game.scores[BLACK] == 1)
    ok, _ = game.play(10, 10)
    check("结束后禁止落子", not ok)


def test_undo_consistency() -> None:
    moves = [(7, 7), (8, 8), (7, 8), (8, 7), (6, 6), (9, 9)]
    game = Game("A", "B")
    for i, (x, y) in enumerate(moves):
        game.play(x, y)
        mirror = Game("A", "B")
        for mx, my in moves[: i + 1]:
            mirror.play(mx, my)
        same = all(
            game.undo_target_for(color) == mirror.undo_target_for(color)
            for color in (BLACK, WHITE)
        )
        if not same:
            check("悔棋目标双方一致", False, f"手数 {i + 1}")
            return
    check("悔棋目标双方一致", True)

    game = Game("A", "B")
    for x, y in moves:
        game.play(x, y)
    target = game.undo_target_for(BLACK)
    game.apply_undo(target)
    check("悔棋后轮次正确", game.turn == BLACK and game.move_count == target)
    check(
        "悔棋后棋子已清除",
        game.board.get(6, 6) == EMPTY and game.board.get(9, 9) == EMPTY and target == 4,
    )

    wrong = Game("A", "B")
    check("空盘不能悔棋", not wrong.can_undo(BLACK)[0])

    lim = Game("A", "B")
    for i in range(4):
        lim.play(i, 0)
        lim.play(i, 1)
    for _ in range(Game("A", "B").undo_limit):
        lim.undo_used[WHITE] += 1
    check("悔棋次数上限生效", not lim.can_undo(WHITE)[0])


def test_ai() -> None:
    board = Board()
    move = ai.choose_move(board, BLACK)
    check("AI 空盘下天元", move == (7, 7), str(move))

    board = Board()
    for x in range(5, 9):
        board.place(x, 7, BLACK)
    move = ai.choose_move(board, WHITE)
    check("AI 拦截四连", move in ((4, 7), (9, 7)), str(move))

    board = Board()
    for x in range(5, 9):
        board.place(x, 7, WHITE)
    move = ai.choose_move(board, WHITE)
    check("AI 自身成五", move in ((4, 7), (9, 7)), str(move))

    board = Board()
    for x, y in [(7, 7), (8, 8), (6, 6)]:
        board.place(x, y, BLACK)
    move = ai.choose_move(board, WHITE)
    ok = move is not None and board.get(*move) == EMPTY
    check("AI 落点合法", ok, str(move))


def test_protocol() -> None:
    raw = protocol.encode("move", {"x": 3, "y": 4}, game=2, seq=9)
    t, d, g, s = protocol.decode(raw.strip())
    check("协议编解码", (t, d["x"], d["y"], g, s) == ("move", 3, 4, 2, 9))

    try:
        protocol.decode(b"{not json}")
        check("坏消息抛错", False)
    except protocol.ProtocolError:
        check("坏消息抛错", True)


def test_transport() -> None:
    left, right = socket.socketpair()
    pa, pb = net.Peer(left).start(), net.Peer(right).start()
    try:
        pa.send("move", {"x": 1, "y": 2})
        msg_type, payload, _g, _s = pb.inbox.get(timeout=2)
        check("TCP 消息到达", msg_type == "move" and payload == {"x": 1, "y": 2})

        pb.send("undo_req", {"target": 3})
        msg_type, payload, _g, _s = pa.inbox.get(timeout=2)
        check("反向消息到达", msg_type == "undo_req" and payload["target"] == 3)

        right.sendall(b"garbage\n")
        msg_type, _payload, _g, _s = pa.inbox.get(timeout=2)
        check("脏数据不崩", msg_type == "__bad__")
    finally:
        pa.close()
        pb.close()


def test_replay() -> None:
    game = Game("甲", "乙")
    for x in range(5):
        game.play(x, 3)
        if game.status == "playing":
            game.play(x, 4)
    with tempfile.TemporaryDirectory() as tmp:
        path = export_replay(game, tmp)
        ok = os.path.exists(path) and os.path.getsize(path) > 0
        check("回放文件生成", ok)
        content = open(path, encoding="utf-8").read()
        check("回放含结果", "获胜" in content)


def test_restart() -> None:
    game = Game("甲", "乙")
    game.play(7, 7)
    game.resign(BLACK)
    swapped = game.restart(True)
    check("重开交换执色", swapped and game.players[BLACK] == "乙")
    check("重开清零棋盘", game.move_count == 0 and game.status == "playing")
    check("重开保留比分", game.scores[WHITE] == 0 and game.scores[BLACK] == 1)


def main() -> int:
    test_win_lines()
    test_rules()
    test_undo_consistency()
    test_ai()
    test_protocol()
    test_transport()
    test_replay()
    test_restart()

    total, passed = len(RESULTS), sum(RESULTS)
    print(f"\n{passed}/{total} 项通过")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
