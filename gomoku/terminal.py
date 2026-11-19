"""终端底层：UTF-8 / ANSI 初始化、光标控制、非阻塞按键读取。

Windows 走 msvcrt，其他平台走 termios raw + select，
统一把按键事件投进队列，主循环非阻塞消费，避免控制台阻塞导致网络不响应。
"""

from __future__ import annotations

import os
import queue
import sys
import threading

IS_WINDOWS = os.name == "nt"

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
UNDERLINE = "\x1b[4m"
REVERSE = "\x1b[7m"

C_TITLE = "\x1b[1;36m"
C_LABEL = "\x1b[90m"
C_DIM = "\x1b[90m"
C_OK = "\x1b[92m"
C_WARN = "\x1b[93m"
C_ERR = "\x1b[91m"
C_HL = "\x1b[1;33m"
C_BLACK = "\x1b[1;36m"
C_WHITE = "\x1b[1;33m"
C_WIN = "\x1b[1;91m"

# 棋子用 ASCII 字母，保证在任何字体/语言环境下等宽，不会把棋盘挤歪
BLACK_GLYPH = "X"
WHITE_GLYPH = "O"


def init_console() -> None:
    """开启 VT 转义并统一成 UTF-8，失败不致命。"""
    if IS_WINDOWS:
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stdin):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def hide_cursor() -> None:
    sys.stdout.write("\x1b[?25l")
    sys.stdout.flush()


def show_cursor() -> None:
    sys.stdout.write("\x1b[?25h")
    sys.stdout.flush()


def clear() -> None:
    sys.stdout.write("\x1b[2J\x1b[H")
    sys.stdout.flush()


def size(default=(100, 30)):
    try:
        s = os.get_terminal_size()
        return s.columns, s.lines
    except OSError:
        return default


class KeyPump:
    """后台线程读按键；主线程用 poll() 取事件。

    事件格式：('UP',) ('DOWN',) ('LEFT',) ('RIGHT',) ('ENTER',) ('ESC',)
              ('BS',) ('CTRL_C',) ('CHAR', 'a')
    """

    def __init__(self):
        self.q: "queue.Queue" = queue.Queue()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="keypump", daemon=True)

    def start(self) -> "KeyPump":
        self._thread.start()
        return self

    def poll(self, timeout: float = 0.0):
        try:
            if timeout and timeout > 0:
                return self.q.get(timeout=timeout)
            return self.q.get_nowait()
        except queue.Empty:
            return None

    wait = poll

    def _emit(self, ch: str) -> None:
        if ch == "\x03":
            self.q.put(("CTRL_C",))
        elif ch in ("\r", "\n"):
            self.q.put(("ENTER",))
        elif ch == "\x1b":
            self.q.put(("ESC",))
        elif ch in ("\x08", "\x7f"):
            self.q.put(("BS",))
        elif ch.isprintable():
            self.q.put(("CHAR", ch))

    def _run(self) -> None:
        try:
            if IS_WINDOWS:
                self._run_windows()
            else:
                self._run_posix()
        except Exception:
            pass

    def _run_windows(self) -> None:
        import msvcrt

        special = {
            "H": ("UP",),
            "P": ("DOWN",),
            "K": ("LEFT",),
            "M": ("RIGHT",),
        }
        while not self._stop:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                key = special.get(msvcrt.getwch())
                if key:
                    self.q.put(key)
                continue
            self._emit(ch)

    def _run_posix(self) -> None:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        backup = termios.tcgetattr(fd)
        tty.setraw(fd)
        arrows = {"[A": ("UP",), "[B": ("DOWN",), "[C": ("RIGHT",), "[D": ("LEFT",)}
        try:
            while not self._stop:
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                raw = os.read(fd, 1)
                if not raw:
                    break
                ch = raw.decode("utf-8", "ignore")
                if ch == "\x1b":
                    seq = ""
                    while len(seq) < 2:
                        more, _, _ = select.select([fd], [], [], 0.02)
                        if not more:
                            break
                        seq += os.read(fd, 1).decode("utf-8", "ignore")
                    key = arrows.get(seq)
                    self.q.put(key if key else ("ESC",))
                    continue
                self._emit(ch)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, backup)
