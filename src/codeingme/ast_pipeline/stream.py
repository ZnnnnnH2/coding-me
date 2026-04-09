"""定义 AST 流式处理辅助结构。"""

from __future__ import annotations


class CodeStreamBuffer:
    def __init__(self) -> None:
        self._chunks: list[str] = []

    def push(self, chunk: str) -> None:
        self._chunks.append(chunk)

    def contents(self) -> str:
        return "".join(self._chunks)
