"""简易 AI：局部形状打分 + 一层搜索。

对每个候选点分别试算「自己下」和「对手下」的价值，取加权最大者。
够用来陪练，不是强力引擎。
"""

from __future__ import annotations

import random

from .board import BLACK, DIRS, EMPTY, WHITE

# (连子数, 开放端数) -> 分值
SHAPE_SCORE = {
    (4, 2): 100000,
    (4, 1): 12000,
    (3, 2): 9000,
    (3, 1): 700,
    (2, 2): 320,
    (2, 1): 70,
    (1, 2): 14,
    (1, 1): 4,
}
WIN_SCORE = 1_000_000


def _line_value(board, x: int, y: int, color: int, dx: int, dy: int) -> int:
    count = 1
    opens = 0
    for sign in (1, -1):
        step = 1
        while True:
            nx, ny = x + dx * step * sign, y + dy * step * sign
            if not board.in_bounds(nx, ny):
                break
            value = board.get(nx, ny)
            if value == color:
                count += 1
                step += 1
            elif value == EMPTY:
                opens += 1
                break
            else:
                break
    if count >= 5:
        return WIN_SCORE
    return SHAPE_SCORE.get((count, opens), 0)


def point_value(board, x: int, y: int, color: int) -> int:
    total = 0
    for dx, dy in DIRS:
        total += _line_value(board, x, y, color, dx, dy)
    return total


def _candidates(board, radius: int = 2) -> list:
    if not board.moves:
        return [(board.size // 2, board.size // 2)]
    seen = set()
    for mx, my, _ in board.moves:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                x, y = mx + dx, my + dy
                if board.in_bounds(x, y) and board.get(x, y) == EMPTY:
                    seen.add((x, y))
    return list(seen)


def choose_move(board, me: int) -> tuple:
    """返回 (x, y)；没有可下点时返回 None。"""
    opp = WHITE if me == BLACK else BLACK
    best = None
    best_score = -1.0

    for x, y in _candidates(board):
        board.set(x, y, me)
        attack = point_value(board, x, y, me)
        board.set(x, y, EMPTY)

        if attack >= WIN_SCORE:
            return x, y

        board.set(x, y, opp)
        defend = point_value(board, x, y, opp)
        board.set(x, y, EMPTY)

        score = attack * 1.05 + defend * 0.95 + random.random() * 3
        if score > best_score:
            best_score = score
            best = (x, y)
    return best
