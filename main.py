"""程序入口。

用法:
    python main.py                # 默认端口 9527
    python main.py --port 9600    # 指定端口（两台机器需一致或手动填写）
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gomoku.app import App  # noqa: E402
from gomoku.net import DEFAULT_PORT  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="控制台五子棋，支持局域网联机")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="联机端口，默认 9527")
    args = parser.parse_args()

    if not (1024 <= args.port <= 65535):
        print("端口范围应为 1024-65535")
        return 2

    if os.name == "nt" and not sys.stdin.isatty():
        print("提示：请在 cmd / Windows Terminal 窗口里直接运行，管道或重定向输入下无法读取按键。")
        return 2

    App(port=args.port).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
