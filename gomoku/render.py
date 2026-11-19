"""棋盘绘制：只负责把状态变成字符行，布局由 app 决定。"""

from __future__ import annotations

from . import terminal as T
from .board import BLACK, EMPTY, LETTERS, WHITE


def _cell(value: int, highlight: bool, cursor: bool, last: bool) -> str:
    if value == EMPTY:
        if cursor:
            return "\x1b[7;33m.\x1b[0m "
        return "\x1b[90m.\x1b[0m "

    glyph = T.BLACK_GLYPH if value == BLACK else T.WHITE_GLYPH
    codes = "1;91" if highlight else ("1;36" if value == BLACK else "1;33")
    if last:
        codes += ";4"
    if cursor:
        codes += ";7"
    return f"\x1b[{codes}m{glyph}\x1b[0m "


def board_lines(game, cursor=None, highlight=()) -> list:
    """返回棋盘的字符行；格子宽 2 字符，所有字形都是 ASCII，等宽安全。"""
    size = game.size
    hl = set(highlight)
    last = game.board.moves[-1][:2] if game.board.moves else None

    lines = ["   " + "".join(f"{ch} " for ch in LETTERS[:size])]
    for y in range(size):
        row = [f"\x1b[90m{y + 1:>2}\x1b[0m "]
        for x in range(size):
            row.append(
                _cell(
                    game.board.get(x, y),
                    (x, y) in hl,
                    cursor == (x, y),
                    last == (x, y),
                )
            )
        lines.append("".join(row))
    return lines


def stone_badge(color: int) -> str:
    if color == BLACK:
        return f"{T.C_BLACK}{T.BLACK_GLYPH}{T.RESET}"
    return f"{T.C_WHITE}{T.WHITE_GLYPH}{T.RESET}"
