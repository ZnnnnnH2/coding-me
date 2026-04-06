from __future__ import annotations

import ast
from dataclasses import dataclass


@dataclass(slots=True)
class ParsedModule:
    source: str
    tree: ast.Module
    file_path: str


class PythonModuleParser:
    def parse(self, source: str, file_path: str = "<memory>") -> ParsedModule:
        return ParsedModule(source=source, tree=ast.parse(source), file_path=file_path)
