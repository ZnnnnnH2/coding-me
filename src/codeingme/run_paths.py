"""定义统一的运行产物保存路径。"""

from __future__ import annotations

from pathlib import Path
import re
from uuid import uuid4


def runs_root(repo_root: Path | str) -> Path:
    return Path(repo_root) / ".codeingme" / "runs"


def create_run_root(
    repo_root: Path | str,
    *,
    source: str,
    case_name: str,
    run_id: str | None = None,
) -> tuple[Path, str]:
    resolved_run_id = run_id or uuid4().hex[:12]
    root = runs_root(repo_root) / _slug(source) / _slug(case_name) / resolved_run_id
    return root, resolved_run_id


def spec_case_name(spec_dir: Path | str) -> str:
    return _slug(Path(spec_dir).name)


def _slug(value: str) -> str:
    collapsed = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip()).strip("._-").lower()
    return collapsed or "custom"
