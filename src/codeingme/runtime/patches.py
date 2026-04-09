"""定义文件补丁模型与补丁应用逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, field
import difflib
from enum import StrEnum
from pathlib import Path
import re


class FilePatchOperation(StrEnum):
    WRITE = "write"
    DELETE = "delete"
    DIFF = "diff"


class PatchConflictError(RuntimeError):
    pass


@dataclass(slots=True)
class FilePatchHunk:
    start_line: int
    expected_lines: list[str] = field(default_factory=list)
    replacement_lines: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FilePatch:
    path: str
    operation: FilePatchOperation = FilePatchOperation.WRITE
    content: str | None = None
    hunks: list[FilePatchHunk] = field(default_factory=list)


@dataclass(slots=True)
class AppliedFilePatch:
    path: str
    operation: FilePatchOperation
    existed: bool
    previous_content: str | None


@dataclass(slots=True)
class FilePatchPlan:
    name: str
    patches: list[FilePatch] = field(default_factory=list)

    def changed_paths(self) -> list[str]:
        return [patch.path for patch in self.patches]

    @classmethod
    def from_unified_diff(cls, name: str, diff_text: str) -> FilePatchPlan:
        lines = diff_text.splitlines()
        patches: list[FilePatch] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.startswith("--- "):
                index += 1
                continue

            if index + 1 >= len(lines) or not lines[index + 1].startswith("+++ "):
                raise ValueError("Unified diff is missing +++ header")
            old_path = _normalize_diff_path(line[4:].strip())
            new_path = _normalize_diff_path(lines[index + 1][4:].strip())
            index += 2

            hunk_specs: list[tuple[int, list[str], list[str]]] = []
            while index < len(lines) and lines[index].startswith("@@ "):
                match = re.match(r"@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@", lines[index])
                if match is None:
                    raise ValueError(f"Invalid unified diff hunk header: {lines[index]}")
                start_line = int(match.group("old"))
                index += 1

                expected_lines: list[str] = []
                replacement_lines: list[str] = []
                while index < len(lines) and not lines[index].startswith("@@ ") and not lines[index].startswith("--- "):
                    entry = lines[index]
                    if entry == r"\ No newline at end of file":
                        index += 1
                        continue
                    if not entry:
                        prefix = " "
                        text = ""
                    else:
                        prefix = entry[0]
                        text = entry[1:]
                    if prefix == " ":
                        expected_lines.append(text)
                        replacement_lines.append(text)
                    elif prefix == "-":
                        expected_lines.append(text)
                    elif prefix == "+":
                        replacement_lines.append(text)
                    else:
                        raise ValueError(f"Unsupported unified diff line: {entry}")
                    index += 1
                hunk_specs.append((start_line, expected_lines, replacement_lines))

            if old_path == "/dev/null":
                content = _compose_content([line for _, _, replacement_lines in hunk_specs for line in replacement_lines])
                patches.append(FilePatch(path=new_path, operation=FilePatchOperation.WRITE, content=content))
                continue
            if new_path == "/dev/null":
                patches.append(FilePatch(path=old_path, operation=FilePatchOperation.DELETE))
                continue

            patches.append(
                FilePatch(
                    path=new_path,
                    operation=FilePatchOperation.DIFF,
                    hunks=[
                        FilePatchHunk(
                            start_line=start_line,
                            expected_lines=expected_lines,
                            replacement_lines=replacement_lines,
                        )
                        for start_line, expected_lines, replacement_lines in hunk_specs
                    ],
                )
            )
        return cls(name=name, patches=patches)


def render_patch_unified_diff(patch: FilePatch, current_content: str | None) -> str:
    previous = current_content or ""
    if patch.operation is FilePatchOperation.WRITE:
        next_content = patch.content or ""
        fromfile = "/dev/null" if current_content is None else f"a/{patch.path}"
        tofile = f"b/{patch.path}"
    elif patch.operation is FilePatchOperation.DELETE:
        next_content = ""
        fromfile = f"a/{patch.path}"
        tofile = "/dev/null"
    elif patch.operation is FilePatchOperation.DIFF:
        if current_content is None:
            raise PatchConflictError(f"Cannot render diff for missing file: {patch.path}")
        next_content = _apply_diff_content(current_content, patch)
        fromfile = f"a/{patch.path}"
        tofile = f"b/{patch.path}"
    else:
        raise ValueError(f"Unsupported patch operation: {patch.operation}")

    return "\n".join(
        difflib.unified_diff(
            previous.splitlines(),
            next_content.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )


def compact_write_plan(root_dir: Path | str, plan: FilePatchPlan) -> FilePatchPlan:
    root_path = Path(root_dir)
    compacted_patches: list[FilePatch] = []
    projected_contents: dict[str, str | None] = {}
    for patch in plan.patches:
        current_content = projected_contents.get(patch.path)
        if patch.path not in projected_contents:
            current_content = _read_existing_content(root_path / patch.path)
        compacted = _compact_patch(patch, current_content)
        if compacted is not None:
            compacted_patches.append(compacted)
        projected_contents[patch.path] = _project_content(current_content, patch)
    return FilePatchPlan(name=plan.name, patches=compacted_patches)


class PatchApplier:
    def __init__(self, root_dir: Path | str) -> None:
        self.root_dir = Path(root_dir)

    def apply(self, plan: FilePatchPlan) -> list[AppliedFilePatch]:
        applied: list[AppliedFilePatch] = []
        for patch in plan.patches:
            target = self.root_dir / patch.path
            existed = target.exists()
            previous_content = target.read_text(encoding="utf-8") if existed else None
            applied.append(
                AppliedFilePatch(
                    path=patch.path,
                    operation=patch.operation,
                    existed=existed,
                    previous_content=previous_content,
                )
            )
            if patch.operation is FilePatchOperation.WRITE:
                self._write_patch(target, patch)
                continue
            if patch.operation is FilePatchOperation.DELETE:
                if target.exists():
                    target.unlink()
                continue
            if patch.operation is FilePatchOperation.DIFF:
                self._apply_diff_patch(target, patch, previous_content)
                continue
            raise ValueError(f"Unsupported patch operation: {patch.operation}")
        return applied

    def _write_patch(self, target: Path, patch: FilePatch) -> None:
        if patch.content is None:
            raise ValueError(f"Missing content for write patch: {patch.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch.content, encoding="utf-8")

    def _apply_diff_patch(self, target: Path, patch: FilePatch, previous_content: str | None) -> None:
        if previous_content is None:
            raise PatchConflictError(f"Cannot apply diff to missing file: {patch.path}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_apply_diff_content(previous_content, patch), encoding="utf-8")


def _normalize_diff_path(path: str) -> str:
    if path in {"a//dev/null", "b//dev/null"}:
        return "/dev/null"
    if path == "/dev/null":
        return path
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _compose_content(lines: list[str], trailing_newline: bool = True) -> str:
    if not lines:
        return ""
    content = "\n".join(lines)
    if trailing_newline:
        content += "\n"
    return content


def _compact_patch(patch: FilePatch, current_content: str | None) -> FilePatch | None:
    if patch.operation is not FilePatchOperation.WRITE or patch.content is None:
        return patch

    if current_content is None:
        return patch

    if current_content == patch.content:
        return None

    if _requires_full_write(current_content, patch.content):
        return patch

    previous_lines = current_content.splitlines()
    replacement_lines = patch.content.splitlines()
    hunks = _build_diff_hunks(previous_lines, replacement_lines)
    if not hunks:
        return None
    return FilePatch(path=patch.path, operation=FilePatchOperation.DIFF, hunks=hunks)


def _requires_full_write(previous_content: str, next_content: str) -> bool:
    return previous_content.endswith("\n") != next_content.endswith("\n")


def _build_diff_hunks(previous_lines: list[str], replacement_lines: list[str]) -> list[FilePatchHunk]:
    matcher = difflib.SequenceMatcher(a=previous_lines, b=replacement_lines, autojunk=False)
    return [
        FilePatchHunk(
            start_line=source_start + 1,
            expected_lines=previous_lines[source_start:source_end],
            replacement_lines=replacement_lines[target_start:target_end],
        )
        for tag, source_start, source_end, target_start, target_end in matcher.get_opcodes()
        if tag != "equal"
    ]


def _apply_diff_content(previous_content: str, patch: FilePatch) -> str:
    current_lines = previous_content.splitlines()
    trailing_newline = previous_content.endswith("\n")
    for hunk in sorted(patch.hunks, key=lambda candidate: candidate.start_line, reverse=True):
        start = max(hunk.start_line - 1, 0)
        end = start + len(hunk.expected_lines)
        current_slice = current_lines[start:end]
        if current_slice != hunk.expected_lines:
            raise PatchConflictError(
                f"Patch conflict for {patch.path} at line {hunk.start_line}: "
                f"expected {hunk.expected_lines!r}, found {current_slice!r}"
            )
        current_lines[start:end] = hunk.replacement_lines
    return _compose_content(current_lines, trailing_newline=trailing_newline)


def _project_content(current_content: str | None, patch: FilePatch) -> str | None:
    if patch.operation is FilePatchOperation.WRITE:
        return patch.content
    if patch.operation is FilePatchOperation.DELETE:
        return None
    if patch.operation is FilePatchOperation.DIFF and current_content is not None:
        return _apply_diff_content(current_content, patch)
    return current_content


def _read_existing_content(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
