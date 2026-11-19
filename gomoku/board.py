"""棋盘数据与胜负判定。

坐标约定：x 为列（0..14，对应 A..O），y 为行（0..14，对应 1..15）。
"""

from __future__ import annotations

EMPTY = 0
BLACK = 1
WHITE = 2

SIZE = 15
DIRS = ((1, 0), (0, 1), (1, 1), (1, -1))
LETTERS = "ABCDEFGHIJKLMNO"


def coord_name(x: int, y: int) -> str:
    """把内部坐标转成 A1 这种人类可读写法。"""
    if not (0 <= x < len(LETTERS)) or y < 0:
        return "??"
    return f"{LETTERS[x]}{y + 1}"


def parse_coord(text: str):
    """把 H8 解析成 (7, 7)，非法时返回 None。"""
    text = (text or "").strip().upper()
    if len(text) < 2:
        return None
    letter = text[0]
    if letter not in LETTERS:
        return None
    try:
        row = int(text[1:])
    except ValueError:
        return None
    if not 1 <= row <= SIZE:
        return None
    return LETTERS.index(letter), row - 1


class Board:
    def __init__(self, size: int = SIZE):
        self.size = size
        self.grid = [[EMPTY] * size for _ in range(size)]
        self.moves = []  # [(x, y, color), ...]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.size and 0 <= y < self.size

    def get(self, x: int, y: int) -> int:
        return self.grid[y][x]

    def set(self, x: int, y: int, color: int) -> None:
        """只改格子，不记入历史（AI 试算用）。"""
        self.grid[y][x] = color

    def place(self, x: int, y: int, color: int) -> None:
        self.grid[y][x] = color
        self.moves.append((x, y, color))

    def pop(self):
        x, y, color = self.moves.pop()
        self.grid[y][x] = EMPTY
        return x, y, color

    def reset(self) -> None:
        self.grid = [[EMPTY] * self.size for _ in range(self.size)]
        self.moves = []

    def full(self) -> bool:
        return len(self.moves) >= self.size * self.size

    def line_at(self, x: int, y: int):
        """若这一手构成五连（含长连），返回连子坐标列表，否则 None。"""
        color = self.get(x, y)
        if color == EMPTY:
            return None
        for dx, dy in DIRS:
            cells = [(x, y)]
            for sign in (1, -1):
                step = 1
                while True:
                    nx, ny = x + dx * step * sign, y + dy * step * sign
                    if not self.in_bounds(nx, ny) or self.get(nx, ny) != color:
                        break
                    cells.append((nx, ny))
                    step += 1
            if len(cells) >= 5:
                cells.sort()
                return cells
        return None

    def snapshot(self):
        return [row[:] for row in self.grid]
