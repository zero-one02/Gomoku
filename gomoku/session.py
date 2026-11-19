"""对局状态机：落子、悔棋、投降、重开、计分、回放导出。

悔棋的目标局面由「请求方颜色 + 当前手数」唯一确定，
双方各自本地计算，结果必然一致，不需要额外同步棋盘。
"""

from __future__ import annotations

import os
import time

from .board import BLACK, EMPTY, LETTERS, SIZE, WHITE, Board

REPLAY_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "replays"
)


def other(color: int) -> int:
    return WHITE if color == BLACK else BLACK


class Game:
    def __init__(self, black_name: str = "黑方", white_name: str = "白方",
                 size: int = SIZE, undo_limit: int = 3):
        self.size = size
        self.board = Board(size)
        self.players = {BLACK: black_name, WHITE: white_name}
        self.first = BLACK
        self.turn = BLACK
        self.status = "playing"  # playing | over
        self.winner = None
        self.reason = None       # five | resign | disconnect | draw
        self.highlight = ()
        self.scores = {BLACK: 0, WHITE: 0}
        self.undo_limit = undo_limit
        self.undo_used = {BLACK: 0, WHITE: 0}
        self.game_no = 1
        self.started_at = time.time()

    # ---------- 基础状态 ----------

    @property
    def move_count(self) -> int:
        return len(self.board.moves)

    @property
    def last_move(self):
        return self.board.moves[-1] if self.board.moves else None

    def color_to_move_at(self, k: int) -> int:
        """第 k 手之后轮到谁走。"""
        return self.first if k % 2 == 0 else other(self.first)

    # ---------- 落子 ----------

    def play(self, x: int, y: int):
        if self.status != "playing":
            return False, "对局已经结束"
        if not self.board.in_bounds(x, y):
            return False, "坐标越界"
        if self.board.get(x, y) != EMPTY:
            return False, "该交叉点已有棋子"

        self.board.place(x, y, self.turn)
        line = self.board.line_at(x, y)
        if line:
            self.status = "over"
            self.winner = self.turn
            self.reason = "five"
            self.highlight = tuple(line)
            self.scores[self.turn] += 1
        elif self.board.full():
            self.status = "over"
            self.winner = None
            self.reason = "draw"
        else:
            self.turn = other(self.turn)
        return True, ""

    # ---------- 悔棋 ----------

    def undo_target_for(self, requester: int):
        """回滚到「轮到 requester 落子」的最近局面（会连带撤掉对方的应手）。"""
        for k in range(self.move_count - 1, -1, -1):
            if self.color_to_move_at(k) == requester:
                return k
        return None

    def can_undo(self, requester: int):
        if self.status != "playing":
            return False, "对局已结束，不能再悔棋"
        if self.undo_used[requester] >= self.undo_limit:
            return False, "本局悔棋次数已用完"
        if self.undo_target_for(requester) is None:
            return False, "还没有可撤回的棋子"
        return True, ""

    def apply_undo(self, target: int) -> None:
        target = max(0, min(int(target), self.move_count))
        while self.move_count > target:
            self.board.pop()
        self.turn = self.color_to_move_at(target)
        if self.status == "over":
            self.status = "playing"
            self.winner = None
            self.reason = None
            self.highlight = ()

    # ---------- 结束与重开 ----------

    def resign(self, color: int) -> None:
        if self.status != "playing":
            return
        self.status = "over"
        self.winner = other(color)
        self.reason = "resign"
        self.scores[self.winner] += 1
        self.highlight = ()

    def mark_disconnected(self) -> None:
        if self.status == "playing":
            self.status = "over"
            self.winner = None
            self.reason = "disconnect"
            self.highlight = ()

    def restart(self, swap: bool = True) -> bool:
        """重开一局；swap 为真时双方交换执黑/执白。返回是否发生了交换。"""
        if swap:
            self.players = {BLACK: self.players[WHITE], WHITE: self.players[BLACK]}
            self.scores = {BLACK: self.scores[WHITE], WHITE: self.scores[BLACK]}
        self.board.reset()
        self.first = BLACK
        self.turn = BLACK
        self.status = "playing"
        self.winner = None
        self.reason = None
        self.highlight = ()
        self.undo_used = {BLACK: 0, WHITE: 0}
        self.game_no += 1
        self.started_at = time.time()
        return swap

    # ---------- 结果文案 ----------

    def result_text(self) -> str:
        if self.reason == "disconnect":
            return "连接中断，本局未分胜负"
        if self.winner is None:
            return "和棋（棋盘已满）"
        how = "五连" if self.reason == "five" else "对方投降"
        return f"{self.players[self.winner]} 获胜（{how}）"


def export_replay(game: Game, directory: str = None) -> str:
    """把落子序列写成文本，返回文件路径。"""
    directory = directory or REPLAY_DIR
    os.makedirs(directory, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(directory, f"replay_{stamp}_g{game.game_no}.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("五子棋对局记录\n")
        fh.write(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"黑方: {game.players[BLACK]}    白方: {game.players[WHITE]}\n")
        fh.write(f"结果: {game.result_text()}\n")
        fh.write(f"比分: {game.scores[BLACK]} : {game.scores[WHITE]}\n")
        fh.write(f"手数: {game.move_count}\n\n")
        for index, (x, y, color) in enumerate(game.board.moves, 1):
            who = "黑" if color == BLACK else "白"
            fh.write(f"{index:>3}. {who} {LETTERS[x]}{y + 1}\n")
    return path
