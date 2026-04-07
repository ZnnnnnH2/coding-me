from __future__ import annotations

import os
from pathlib import Path


DEFAULT_DOTENV_NAME = ".env"


def load_project_dotenv(*, override: bool = True) -> Path | None:
    path = project_dotenv_path()
    if path is None:
        return None
    for key, value in parse_dotenv(path).items():
        if override or key not in os.environ:
            os.environ[key] = value
    return path


def project_dotenv_path(start: Path | str | None = None) -> Path | None:
    explicit_path = os.getenv("CODEINGME_DOTENV_PATH")
    if explicit_path:
        candidate = Path(explicit_path).expanduser().resolve()
        return candidate if candidate.is_file() else None

    search_root = Path(start).resolve() if start is not None else Path.cwd().resolve()
    candidates: list[Path] = [(search_root / DEFAULT_DOTENV_NAME).resolve()]
    candidates.extend(directory / DEFAULT_DOTENV_NAME for directory in search_root.parents)
    candidates.append(Path(__file__).resolve().parents[2] / DEFAULT_DOTENV_NAME)

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def parse_dotenv(path: Path | str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values
