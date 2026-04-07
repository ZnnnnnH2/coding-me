from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import importlib.util
import inspect
from pathlib import Path
import threading
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .orchestrator import CodeingmeOrchestrator, OrchestrationEvent
from .spec_parser import load_spec_bundle


REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_SPEC_FILES = {
    "openapi.yaml": "openapi",
    "openapi.yml": "openapi",
    "openapi.json": "openapi",
    "schema.sql": "schema",
    "business_rules.yaml": "rules",
    "business_rules.yml": "rules",
    "rules.yaml": "rules",
    "user_story.md": "user_story",
    "README.md": "user_story",
}
CANONICAL_SPEC_FILES = {
    "openapi": "openapi.yaml",
    "schema": "schema.sql",
    "rules": "business_rules.yaml",
    "user_story": "user_story.md",
}
TERMINAL_RUN_STATES = {"succeeded", "failed"}


class StudioRunRequest(BaseModel):
    preset_name: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


@dataclass(slots=True)
class StudioRunRecord:
    run_id: str
    status: str
    created_at: str
    updated_at: str
    current_state: str
    current_message: str
    requirement: str
    bundle: dict[str, object]
    run_root: Path
    workspace_root: Path
    spec_dir: Path
    events: list[dict[str, object]] = field(default_factory=list)
    result: dict[str, object] | None = None
    error: str | None = None
    files: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_state": self.current_state,
            "current_message": self.current_message,
            "requirement": self.requirement,
            "bundle": copy.deepcopy(self.bundle),
            "events": copy.deepcopy(self.events),
            "result": copy.deepcopy(self.result),
            "error": self.error,
            "files": copy.deepcopy(self.files),
            "workspace_root": str(self.workspace_root),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudioRunManager:
    def __init__(
        self,
        *,
        repo_root: Path | str | None = None,
        orchestrator_factory: Callable[[Path], CodeingmeOrchestrator] | None = None,
        run_inline: bool = False,
    ) -> None:
        self.repo_root = Path(repo_root) if repo_root is not None else REPO_ROOT
        self.specs_root = self.repo_root / "specs"
        self.run_root = self.repo_root / ".codeingme" / "studio_runs"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.orchestrator_factory = orchestrator_factory or (
            lambda workspace_root: CodeingmeOrchestrator(workspace_root=workspace_root)
        )
        self.run_inline = run_inline
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="codeingme-studio")
        self._runs: dict[str, StudioRunRecord] = {}
        self._lock = threading.Lock()

    def list_presets(self) -> list[dict[str, object]]:
        if not self.specs_root.exists():
            return []
        presets: list[dict[str, object]] = []
        for spec_dir in sorted(path for path in self.specs_root.iterdir() if path.is_dir()):
            bundle = load_spec_bundle(spec_dir)
            presets.append(
                {
                    "name": spec_dir.name,
                    "display_name": _display_name(spec_dir.name),
                    "service_name": bundle.service_name,
                    "summary": bundle.summary,
                    "endpoints": bundle.endpoints,
                    "tables": bundle.tables,
                }
            )
        return presets

    def get_preset(self, preset_name: str) -> dict[str, object]:
        preset_dir = (self.specs_root / preset_name).resolve()
        if not preset_dir.exists() or not preset_dir.is_dir():
            raise FileNotFoundError(f"Preset not found: {preset_name}")
        bundle = load_spec_bundle(preset_dir)
        return {
            "name": preset_name,
            "display_name": _display_name(preset_name),
            "bundle": bundle.to_dict(),
            "files": {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted(preset_dir.iterdir())
                if path.is_file()
            },
        }

    def create_run(self, payload: StudioRunRequest) -> dict[str, object]:
        files = self._resolve_request_files(payload)
        run_id = uuid4().hex[:12]
        run_root = self.run_root / run_id
        spec_dir = run_root / "spec_bundle"
        workspace_root = run_root / "workspace"
        spec_dir.mkdir(parents=True, exist_ok=True)
        workspace_root.mkdir(parents=True, exist_ok=True)

        for name, content in files.items():
            (spec_dir / name).write_text(content, encoding="utf-8")
        bundle = load_spec_bundle(spec_dir)
        requirement = bundle.requirement_prompt()

        record = StudioRunRecord(
            run_id=run_id,
            status="queued",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            current_state="intake",
            current_message="Bundle accepted and waiting for the generation pipeline to start.",
            requirement=requirement,
            bundle=bundle.to_dict(),
            run_root=run_root,
            workspace_root=workspace_root,
            spec_dir=spec_dir,
        )
        self._append_manual_event(
            record,
            stage="run",
            status="queued",
            message="Queued the uploaded specification bundle for orchestration.",
            state="intake",
        )
        with self._lock:
            self._runs[run_id] = record

        if self.run_inline:
            self._execute_run(run_id)
        else:
            self._executor.submit(self._execute_run, run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, object]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            return record.to_dict()

    def read_run_file(self, run_id: str, relative_path: str) -> str:
        _, candidate = self._resolve_run_file(run_id, relative_path)
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate.read_text(encoding="utf-8")

    def render_run_preview(self, run_id: str, relative_path: str) -> str:
        record, candidate = self._resolve_run_file(run_id, relative_path)
        if candidate.suffix.lower() != ".html":
            raise ValueError("Preview is only available for generated HTML files.")
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        rendered = self._render_html_from_workspace(record.workspace_root, relative_path)
        if rendered is not None:
            return rendered
        source = candidate.read_text(encoding="utf-8")
        if "{{ITEM_ROWS}}" in source:
            return source.replace(
                "{{ITEM_ROWS}}",
                '<li data-completed="false">Preview placeholder: runtime row rendering was not available.</li>',
            )
        return source

    def _execute_run(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.status = "running"
            record.updated_at = _utc_now()
        self._append_manual_event(
            record,
            stage="run",
            status="started",
            message="Studio launched a dedicated generation workspace for this run.",
            state="intake",
        )

        def on_event(event: OrchestrationEvent) -> None:
            payload = asdict(event)
            with self._lock:
                current = self._runs[run_id]
                payload["sequence"] = len(current.events) + 1
                current.events.append(payload)
                current.updated_at = _utc_now()
                current.current_message = event.message
                if event.state is not None:
                    current.current_state = event.state

        try:
            orchestrator = self.orchestrator_factory(record.workspace_root)
            result = orchestrator.run(record.requirement, event_callback=on_event)
            files = self._collect_workspace_files(record.workspace_root)
            with self._lock:
                current = self._runs[run_id]
                current.status = "succeeded"
                current.updated_at = _utc_now()
                current.current_state = result.final_state
                current.current_message = "Run completed successfully."
                current.result = asdict(result)
                current.files = files
        except Exception as exc:
            files = self._collect_workspace_files(record.workspace_root)
            with self._lock:
                current = self._runs[run_id]
                current.status = "failed"
                current.updated_at = _utc_now()
                current.current_message = str(exc)
                current.error = str(exc)
                current.files = files
                current.events.append(
                    {
                        "sequence": len(current.events) + 1,
                        "timestamp": _utc_now(),
                        "stage": "run",
                        "status": "failed",
                        "message": str(exc),
                        "state": current.current_state,
                        "role": None,
                        "batch": None,
                        "details": {},
                    }
                )

    def _append_manual_event(
        self,
        record: StudioRunRecord,
        *,
        stage: str,
        status: str,
        message: str,
        state: str | None = None,
    ) -> None:
        record.events.append(
            {
                "sequence": len(record.events) + 1,
                "timestamp": _utc_now(),
                "stage": stage,
                "status": status,
                "message": message,
                "state": state,
                "role": None,
                "batch": None,
                "details": {},
                }
            )

    def _resolve_run_file(self, run_id: str, relative_path: str) -> tuple[StudioRunRecord, Path]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            candidate = (record.workspace_root / relative_path).resolve()
            workspace_root = record.workspace_root.resolve()
        if workspace_root not in candidate.parents and candidate != workspace_root:
            raise ValueError("Requested file path escapes the workspace root.")
        return record, candidate

    def _render_html_from_workspace(self, workspace_root: Path, relative_path: str) -> str | None:
        backend_root = workspace_root / "demo_app"
        if not backend_root.exists():
            return None
        template_name = Path(relative_path).name
        for module_path in sorted(backend_root.glob("*_api.py")):
            try:
                module_source = module_path.read_text(encoding="utf-8")
            except OSError:
                continue
            if template_name not in module_source:
                continue
            rendered = self._render_html_with_module(module_path)
            if rendered is not None:
                return rendered
        return None

    def _render_html_with_module(self, module_path: Path) -> str | None:
        module_name = f"codeingme_studio_preview_{module_path.stem}_{uuid4().hex}"
        try:
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            return None

        renderer = getattr(module, "_render_item_list", None)
        if callable(renderer):
            try:
                rendered = renderer()
            except Exception:
                rendered = None
            if isinstance(rendered, str) and rendered.strip():
                return rendered

        home_handler = getattr(module, "item_list", None)
        if not callable(home_handler):
            return None
        try:
            response = home_handler()
            if inspect.isawaitable(response):
                response = asyncio.run(response)
        except Exception:
            return None
        if isinstance(response, HTMLResponse):
            return response.body.decode("utf-8")
        if isinstance(response, str) and response.strip():
            return response
        return None

    def _resolve_request_files(self, payload: StudioRunRequest) -> dict[str, str]:
        if payload.preset_name:
            preset = self.get_preset(payload.preset_name)
            return {
                name: content
                for name, content in preset["files"].items()
                if isinstance(content, str) and content.strip()
            }

        normalized: dict[str, str] = {}
        for incoming_name, content in payload.files.items():
            name = Path(incoming_name).name
            slot = SUPPORTED_SPEC_FILES.get(name)
            if slot is None:
                raise ValueError(
                    f"Unsupported file: {incoming_name}. "
                    f"Expected one of {', '.join(sorted(SUPPORTED_SPEC_FILES))}."
                )
            if not content.strip():
                continue
            normalized[CANONICAL_SPEC_FILES[slot]] = content
        if not normalized:
            raise ValueError("No supported specification files were provided.")
        return normalized

    def _collect_workspace_files(self, workspace_root: Path) -> list[dict[str, object]]:
        if not workspace_root.exists():
            return []
        files: list[dict[str, object]] = []
        for path in sorted(workspace_root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace_root)
            if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
                continue
            files.append(
                {
                    "path": relative.as_posix(),
                    "size": path.stat().st_size,
                    "language": _language_for_path(path),
                }
            )
        return files


def _language_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".py": "python",
        ".html": "html",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".sql": "sql",
        ".md": "markdown",
        ".txt": "text",
        ".log": "text",
    }.get(suffix, "text")


def _display_name(value: str) -> str:
    parts = [part for part in value.replace("-", "_").split("_") if part]
    return " ".join(part.capitalize() for part in parts) or value


def create_app(run_manager: StudioRunManager | None = None) -> FastAPI:
    manager = run_manager or StudioRunManager()
    app = FastAPI(title="Codeingme Studio", version="0.1.0")
    app.state.run_manager = manager

    @app.get("/", response_class=HTMLResponse)
    def studio_home() -> HTMLResponse:
        return HTMLResponse(STUDIO_HTML)

    @app.get("/api/studio/presets")
    def list_presets() -> dict[str, object]:
        return {"presets": manager.list_presets()}

    @app.get("/api/studio/presets/{preset_name}")
    def get_preset(preset_name: str) -> dict[str, object]:
        try:
            return manager.get_preset(preset_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/studio/runs")
    def create_run(payload: StudioRunRequest) -> dict[str, object]:
        try:
            return manager.create_run(payload)
        except (FileNotFoundError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/studio/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, object]:
        try:
            return manager.get_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc

    @app.get("/api/studio/runs/{run_id}/file", response_class=PlainTextResponse)
    def get_run_file(run_id: str, path: str = Query(..., min_length=1)) -> PlainTextResponse:
        try:
            return PlainTextResponse(manager.read_run_file(run_id, path))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/studio/runs/{run_id}/preview", response_class=HTMLResponse)
    def preview_run_file(run_id: str, path: str = Query(..., min_length=1)) -> HTMLResponse:
        try:
            return HTMLResponse(manager.render_run_preview(run_id, path))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="Launch the Codeingme Studio demo UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)
    if args.reload:
        uvicorn.run("codeingme.studio:app", host=args.host, port=args.port, reload=True)
    else:
        uvicorn.run(app, host=args.host, port=args.port, reload=False)
    return 0


STUDIO_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Codeingme Studio</title>
    <style>
      :root {
        --bg: #f6efe5;
        --panel: rgba(255, 249, 240, 0.82);
        --panel-strong: rgba(255, 246, 232, 0.94);
        --ink: #17211f;
        --muted: #586160;
        --line: rgba(42, 51, 49, 0.12);
        --accent: #c85d2f;
        --accent-deep: #8f3410;
        --teal: #0f7c78;
        --gold: #d39b24;
        --shadow: 0 24px 70px rgba(79, 44, 23, 0.16);
        --radius: 24px;
        --mono: "IBM Plex Mono", "SFMono-Regular", "Consolas", monospace;
        --serif: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", Georgia, serif;
        --sans: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
      }

      * {
        box-sizing: border-box;
      }

      body {
        margin: 0;
        font-family: var(--sans);
        color: var(--ink);
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(200, 93, 47, 0.22), transparent 32%),
          radial-gradient(circle at top right, rgba(15, 124, 120, 0.16), transparent 26%),
          linear-gradient(180deg, #fffaf2 0%, #f4ebdf 48%, #efe4d7 100%);
      }

      body::before {
        content: "";
        position: fixed;
        inset: 0;
        pointer-events: none;
        background:
          linear-gradient(125deg, rgba(255,255,255,0.32), transparent 45%),
          repeating-linear-gradient(
            -45deg,
            rgba(23, 33, 31, 0.018) 0,
            rgba(23, 33, 31, 0.018) 1px,
            transparent 1px,
            transparent 16px
          );
        opacity: 0.85;
      }

      .shell {
        position: relative;
        padding: 28px;
      }

      .hero {
        display: grid;
        grid-template-columns: 1.35fr 0.9fr;
        gap: 18px;
        margin-bottom: 20px;
      }

      .hero-card,
      .panel {
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: var(--radius);
        backdrop-filter: blur(16px);
        box-shadow: var(--shadow);
      }

      .hero-card {
        padding: 24px 26px;
      }

      .hero h1 {
        margin: 0 0 12px;
        font-family: var(--serif);
        font-size: clamp(2.6rem, 3.8vw, 4.6rem);
        line-height: 0.95;
        letter-spacing: -0.05em;
      }

      .hero p {
        margin: 0;
        max-width: 54rem;
        color: var(--muted);
        font-size: 1.03rem;
        line-height: 1.65;
      }

      .signal-board {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }

      .signal {
        padding: 16px 18px;
        background: var(--panel-strong);
        border-radius: 20px;
        border: 1px solid rgba(42, 51, 49, 0.08);
      }

      .signal strong {
        display: block;
        font-size: 0.8rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 8px;
      }

      .signal span {
        font-family: var(--serif);
        font-size: 1.5rem;
      }

      .layout {
        display: grid;
        grid-template-columns: 1.02fr 1.26fr;
        gap: 18px;
      }

      .panel {
        padding: 22px;
      }

      .panel-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 18px;
      }

      .eyebrow {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        color: var(--accent-deep);
        font-size: 0.76rem;
        letter-spacing: 0.18em;
        text-transform: uppercase;
      }

      .eyebrow::before {
        content: "";
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: linear-gradient(135deg, var(--accent), var(--gold));
        box-shadow: 0 0 0 6px rgba(200, 93, 47, 0.12);
      }

      h2 {
        margin: 8px 0 0;
        font-family: var(--serif);
        font-size: 1.9rem;
        line-height: 1.1;
      }

      .controls {
        display: grid;
        gap: 14px;
      }

      label {
        display: block;
        font-size: 0.88rem;
        font-weight: 600;
        margin-bottom: 8px;
      }

      select,
      textarea,
      button,
      .dropzone {
        width: 100%;
        border-radius: 18px;
        border: 1px solid rgba(42, 51, 49, 0.12);
        background: rgba(255, 252, 246, 0.92);
        color: var(--ink);
        font: inherit;
      }

      select,
      textarea {
        padding: 14px 16px;
      }

      textarea {
        min-height: 164px;
        resize: vertical;
        font-family: var(--mono);
        font-size: 0.84rem;
        line-height: 1.6;
      }

      .dropzone {
        position: relative;
        padding: 18px;
        border-style: dashed;
        display: grid;
        gap: 6px;
      }

      .dropzone input {
        position: absolute;
        inset: 0;
        opacity: 0;
        cursor: pointer;
      }

      .dropzone strong {
        font-size: 1rem;
      }

      .dropzone span {
        color: var(--muted);
        line-height: 1.5;
      }

      .two-up {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }

      .button-row {
        display: flex;
        gap: 12px;
        flex-wrap: wrap;
      }

      button {
        border: 0;
        padding: 14px 18px;
        cursor: pointer;
        transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease;
      }

      button:hover {
        transform: translateY(-1px);
      }

      .button-primary {
        background: linear-gradient(135deg, var(--accent), #db7c47);
        color: white;
        box-shadow: 0 16px 32px rgba(200, 93, 47, 0.26);
      }

      .button-secondary {
        background: rgba(255, 252, 246, 0.92);
      }

      button:disabled {
        cursor: wait;
        opacity: 0.55;
        transform: none;
      }

      .bundle-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }

      .bundle-card {
        padding: 14px;
        border-radius: 20px;
        border: 1px solid rgba(42, 51, 49, 0.08);
        background: rgba(255, 252, 246, 0.7);
      }

      .bundle-card header {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 12px;
        margin-bottom: 8px;
      }

      .bundle-card small {
        color: var(--muted);
      }

      .status-strip {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 12px 14px;
        border-radius: 18px;
        background: rgba(15, 124, 120, 0.08);
        color: #0d605c;
        border: 1px solid rgba(15, 124, 120, 0.14);
      }

      .status-strip[data-status="failed"] {
        background: rgba(185, 53, 44, 0.08);
        color: #8d261d;
        border-color: rgba(185, 53, 44, 0.16);
      }

      .status-strip[data-status="running"] {
        background: rgba(211, 155, 36, 0.11);
        color: #7f5a0d;
        border-color: rgba(211, 155, 36, 0.18);
      }

      .state-rail {
        display: grid;
        grid-template-columns: repeat(9, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 18px;
      }

      .state-pill {
        position: relative;
        padding: 12px 10px;
        border-radius: 18px;
        background: rgba(255, 252, 246, 0.72);
        border: 1px solid rgba(42, 51, 49, 0.08);
        text-align: center;
        font-size: 0.78rem;
        line-height: 1.35;
        color: var(--muted);
      }

      .state-pill.active {
        color: #fff;
        background: linear-gradient(135deg, var(--teal), #159f96);
        box-shadow: 0 14px 30px rgba(15, 124, 120, 0.22);
      }

      .state-pill.active::after {
        content: "";
        position: absolute;
        inset: auto 18px -8px;
        height: 8px;
        border-radius: 999px;
        background: rgba(21, 159, 150, 0.24);
        animation: pulse 1.5s ease-in-out infinite;
      }

      .state-pill.done {
        color: #fef8ed;
        background: linear-gradient(135deg, #c07a18, #d39b24);
      }

      .state-pill.recovery {
        color: #fff7f0;
        background: linear-gradient(135deg, var(--accent-deep), var(--accent));
      }

      @keyframes pulse {
        0%, 100% { opacity: 0.25; transform: scaleX(0.92); }
        50% { opacity: 0.9; transform: scaleX(1); }
      }

      .observer-grid {
        display: grid;
        grid-template-columns: 1.2fr 0.94fr;
        gap: 14px;
      }

      .log-column,
      .artifact-column {
        display: grid;
        gap: 14px;
      }

      .surface {
        background: rgba(255, 252, 246, 0.76);
        border: 1px solid rgba(42, 51, 49, 0.08);
        border-radius: 22px;
        padding: 16px;
      }

      .surface h3 {
        margin: 0 0 14px;
        font-family: var(--serif);
        font-size: 1.3rem;
      }

      .event-feed {
        display: grid;
        gap: 12px;
        max-height: 560px;
        overflow: auto;
        padding-right: 4px;
      }

      .event-card {
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid rgba(42, 51, 49, 0.08);
        animation: rise 320ms ease;
      }

      @keyframes rise {
        from {
          opacity: 0;
          transform: translateY(10px);
        }
        to {
          opacity: 1;
          transform: translateY(0);
        }
      }

      .event-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 8px;
      }

      .tag {
        display: inline-flex;
        align-items: center;
        padding: 4px 8px;
        border-radius: 999px;
        font-size: 0.72rem;
        letter-spacing: 0.08em;
        text-transform: uppercase;
        background: rgba(23, 33, 31, 0.06);
        color: var(--muted);
      }

      .tag[data-tone="active"] {
        background: rgba(15, 124, 120, 0.14);
        color: #0b605d;
      }

      .tag[data-tone="success"] {
        background: rgba(200, 93, 47, 0.14);
        color: var(--accent-deep);
      }

      .event-card strong {
        display: block;
        margin-bottom: 6px;
      }

      .event-card p {
        margin: 0;
        color: var(--muted);
        line-height: 1.55;
      }

      .metric-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }

      .metric {
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.68);
        border: 1px solid rgba(42, 51, 49, 0.08);
      }

      .metric strong {
        display: block;
        font-size: 0.76rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 6px;
      }

      .metric span {
        font-family: var(--serif);
        font-size: 1.2rem;
      }

      .insight-grid,
      .artifact-grid,
      .batch-stack {
        display: grid;
        gap: 12px;
      }

      .panel-copy {
        margin: 0;
        color: var(--muted);
        line-height: 1.6;
      }

      .summary-grid {
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 10px;
      }

      .summary-card,
      .batch-card,
      .artifact-card,
      .artifact-group,
      .detail-card {
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.68);
        border: 1px solid rgba(42, 51, 49, 0.08);
      }

      .summary-card strong,
      .detail-card strong {
        display: block;
        font-size: 0.75rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 6px;
      }

      .summary-card span {
        display: block;
        font-family: var(--serif);
        font-size: 1.2rem;
      }

      .batch-card header,
      .artifact-card header {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 10px;
      }

      .batch-card h4,
      .artifact-card h4 {
        margin: 0;
        font-size: 1rem;
      }

      .batch-node-list,
      .detail-list {
        display: grid;
        gap: 10px;
      }

      .batch-node {
        padding: 12px;
        border-radius: 16px;
        background: rgba(246, 239, 229, 0.62);
        border: 1px solid rgba(42, 51, 49, 0.07);
      }

      .batch-node code,
      .chip {
        font-family: var(--mono);
        font-size: 0.77rem;
      }

      .batch-meta {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-top: 8px;
      }

      .chip-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .chip {
        display: inline-flex;
        align-items: center;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(23, 33, 31, 0.06);
        color: var(--ink);
      }

      .artifact-group strong {
        display: block;
        font-size: 0.76rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 8px;
      }

      .artifact-rows {
        display: grid;
        gap: 8px;
      }

      .artifact-row {
        display: grid;
        gap: 4px;
      }

      .artifact-row span {
        font-size: 0.75rem;
        letter-spacing: 0.1em;
        text-transform: uppercase;
        color: var(--muted);
      }

      .artifact-row code,
      .artifact-row pre {
        margin: 0;
        padding: 0;
        background: transparent;
        color: var(--ink);
        min-height: 0;
        white-space: pre-wrap;
        overflow: visible;
      }

      .mini-json {
        font-family: var(--mono);
        font-size: 0.75rem;
        line-height: 1.55;
      }

      .agent-grid {
        display: grid;
        grid-template-columns: minmax(220px, 0.85fr) minmax(0, 1.15fr);
        gap: 12px;
      }

      .agent-list,
      .agent-detail,
      .agent-event-list {
        display: grid;
        gap: 10px;
      }

      .agent-button {
        width: 100%;
        text-align: left;
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.7);
        border: 1px solid rgba(42, 51, 49, 0.08);
        box-shadow: none;
      }

      .agent-button:hover {
        transform: translateY(-1px);
      }

      .agent-button.is-active {
        background: linear-gradient(135deg, var(--ink), #2f423f);
        color: #fff9f0;
      }

      .agent-button.is-active .agent-button__summary,
      .agent-button.is-active .agent-button__meta,
      .agent-button.is-active .agent-button__meta .tag {
        color: rgba(255, 249, 240, 0.88);
      }

      .agent-button__top,
      .agent-detail__header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
      }

      .agent-button__name,
      .agent-detail__title {
        margin: 0;
        font-size: 1rem;
      }

      .agent-button__summary,
      .agent-detail__copy {
        margin: 0;
        color: var(--muted);
        line-height: 1.6;
        font-size: 0.9rem;
      }

      .agent-button__meta,
      .file-chip-row {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      .agent-detail__copy {
        margin-top: 4px;
      }

      .agent-section {
        padding: 14px;
        border-radius: 18px;
        background: rgba(255, 255, 255, 0.68);
        border: 1px solid rgba(42, 51, 49, 0.08);
      }

      .agent-section strong {
        display: block;
        font-size: 0.76rem;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--muted);
        margin-bottom: 8px;
      }

      .agent-event-card {
        padding: 12px;
        border-radius: 16px;
        background: rgba(246, 239, 229, 0.62);
        border: 1px solid rgba(42, 51, 49, 0.07);
      }

      .agent-event-card p {
        margin: 8px 0 0;
        color: var(--muted);
        line-height: 1.55;
      }

      .file-chip-button {
        width: auto;
        padding: 9px 11px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.8);
      }

      .file-chip-button.is-active {
        background: linear-gradient(135deg, var(--accent), #db7c47);
        color: white;
      }

      .file-list {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        margin-bottom: 12px;
      }

      .file-list button {
        width: auto;
        padding: 10px 12px;
        background: rgba(255, 255, 255, 0.74);
      }

      .file-list button.active {
        background: linear-gradient(135deg, var(--accent), #db7c47);
        color: white;
      }

      .file-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        flex-wrap: wrap;
        margin-bottom: 12px;
      }

      .viewer-toggle {
        display: inline-flex;
        gap: 8px;
        padding: 5px;
        border-radius: 999px;
        background: rgba(255, 255, 255, 0.72);
        border: 1px solid rgba(42, 51, 49, 0.08);
      }

      .viewer-toggle button {
        width: auto;
        padding: 9px 14px;
        border-radius: 999px;
        background: transparent;
        box-shadow: none;
      }

      .viewer-toggle button.is-active {
        background: linear-gradient(135deg, var(--teal), #159f96);
        color: white;
      }

      .preview-hint {
        margin: 0 0 12px;
        color: var(--muted);
        line-height: 1.5;
      }

      .preview-frame {
        width: 100%;
        min-height: 420px;
        border: 1px solid rgba(42, 51, 49, 0.08);
        border-radius: 18px;
        background: white;
      }

      pre {
        margin: 0;
        padding: 14px;
        min-height: 240px;
        border-radius: 18px;
        background: #17211f;
        color: #f8f1e5;
        overflow: auto;
        font-family: var(--mono);
        font-size: 0.8rem;
        line-height: 1.65;
      }

      .log-box {
        max-height: 220px;
      }

      .muted {
        color: var(--muted);
      }

      .split {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 12px;
      }

      @media (max-width: 1200px) {
        .layout,
        .hero,
        .observer-grid {
          grid-template-columns: 1fr;
        }

        .agent-grid {
          grid-template-columns: 1fr;
        }

        .bundle-grid,
        .two-up,
        .state-rail,
        .split,
        .metric-grid,
        .summary-grid {
          grid-template-columns: 1fr;
        }
      }

      @media (max-width: 720px) {
        .shell {
          padding: 16px;
        }

        .panel,
        .hero-card {
          padding: 18px;
        }
      }
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <article class="hero-card">
          <div class="eyebrow">Specification Control Room</div>
          <h1>Codeingme Studio</h1>
          <p>
            Load structured requirement, API, schema, and rule inputs, trigger the generation pipeline,
            and inspect each agent while the system drafts tests, generates the backend
            module, syncs the graph, and verifies the result.
          </p>
        </article>
        <article class="hero-card">
          <div class="signal-board">
            <div class="signal">
              <strong>Current State</strong>
              <span id="hero-state">Intake</span>
            </div>
            <div class="signal">
              <strong>Run Status</strong>
              <span id="hero-status">Idle</span>
            </div>
            <div class="signal">
              <strong>Event Count</strong>
              <span id="hero-events">0</span>
            </div>
            <div class="signal">
              <strong>Generated Files</strong>
              <span id="hero-files">0</span>
            </div>
          </div>
        </article>
      </section>

      <main class="layout">
        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="eyebrow">Specification Intake</div>
              <h2>Load Structured Specification Inputs</h2>
            </div>
          </div>

          <div class="controls">
            <div class="two-up">
              <div>
                <label for="preset-select">Example Specification Bundle</label>
                <select id="preset-select"></select>
              </div>
              <div>
                <label for="file-loader">Import Supported Files</label>
                <div class="dropzone">
                  <strong>Drop in OpenAPI, schema, rules, and story files</strong>
                  <span>
                    Supported names: <code>openapi.yaml</code>, <code>schema.sql</code>,
                    <code>business_rules.yaml</code>, <code>user_story.md</code>
                  </span>
                  <input id="file-loader" type="file" multiple />
                </div>
              </div>
            </div>

            <div class="button-row">
              <button class="button-secondary" id="load-preset">Load Example Into Editors</button>
              <button class="button-secondary" id="clear-editors">Clear Editors</button>
              <button class="button-primary" id="start-run">Run Generation Pipeline</button>
            </div>

            <div id="status-strip" class="status-strip" data-status="idle">
              Waiting for structured inputs. Load an example bundle or import your own files to begin.
            </div>

            <div class="bundle-grid">
              <article class="bundle-card">
                <header>
                  <strong>OpenAPI</strong>
                  <small>openapi.yaml</small>
                </header>
                <textarea id="openapi-input" spellcheck="false"></textarea>
              </article>
              <article class="bundle-card">
                <header>
                  <strong>Schema</strong>
                  <small>schema.sql</small>
                </header>
                <textarea id="schema-input" spellcheck="false"></textarea>
              </article>
              <article class="bundle-card">
                <header>
                  <strong>Business Rules</strong>
                  <small>business_rules.yaml</small>
                </header>
                <textarea id="rules-input" spellcheck="false"></textarea>
              </article>
              <article class="bundle-card">
                <header>
                  <strong>User Story</strong>
                  <small>user_story.md</small>
                </header>
                <textarea id="story-input" spellcheck="false"></textarea>
              </article>
            </div>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div>
              <div class="eyebrow">Run Observability</div>
              <h2>See What The Agents Are Doing</h2>
            </div>
          </div>

          <div id="state-rail" class="state-rail"></div>

          <div class="observer-grid">
            <div class="log-column">
              <section class="surface">
                <h3>Live Event Feed</h3>
                <div id="event-feed" class="event-feed">
                  <div class="event-card">
                    <div class="event-meta">
                      <span class="tag" data-tone="active">idle</span>
                    </div>
                    <strong>No run yet</strong>
                    <p>Start a run and the generation pipeline will stream state transitions, agent activity, and verification steps here.</p>
                  </div>
                </div>
              </section>
            </div>

            <div class="artifact-column">
              <section class="surface">
                <h3>Agent Trace</h3>
                <div class="agent-grid">
                  <div id="agent-list" class="agent-list">
                    <p class="panel-copy muted">
                      Start a run to inspect each agent, its latest status, and the files it emitted.
                    </p>
                  </div>
                  <div id="agent-detail" class="agent-detail">
                    <p class="panel-copy muted">
                      Select an agent to inspect its status, context, outputs, and related run events.
                    </p>
                  </div>
                </div>
              </section>

              <section class="surface">
                <h3>Run Snapshot</h3>
                <div id="metric-grid" class="metric-grid"></div>
              </section>

              <section class="surface">
                <h3>Cascade Map</h3>
                <div id="cascade-panel" class="insight-grid">
                  <p class="panel-copy muted">
                    Start a run to see impacted nodes, cascade batches, and role assignments across the repair plan.
                  </p>
                </div>
              </section>

              <section class="surface">
                <h3>Graph Slice</h3>
                <div id="graph-panel" class="insight-grid">
                  <p class="panel-copy muted">
                    Graph nodes, synchronization deltas, and the focused context slice will appear here after orchestration begins.
                  </p>
                </div>
              </section>

              <section class="surface">
                <h3>Artifact Manifest</h3>
                <div id="artifact-panel" class="artifact-grid">
                  <p class="panel-copy muted">
                    Agent-emitted artifacts, routes, files, and risk notes will be summarized here for the current run.
                  </p>
                </div>
              </section>

              <section class="surface">
                <h3>Generated Files</h3>
                <div id="file-list" class="file-list"></div>
                <div class="file-toolbar">
                  <div id="file-meta" class="muted">No generated file selected yet.</div>
                  <div class="viewer-toggle">
                    <button id="source-view-button" class="is-active" type="button">Source</button>
                    <button id="preview-view-button" type="button">Preview</button>
                  </div>
                </div>
                <p id="preview-hint" class="preview-hint">Source is available for every generated file. Preview only appears for generated HTML files.</p>
                <pre id="file-viewer">No generated file selected yet.</pre>
                <iframe id="file-preview" class="preview-frame" title="Generated HTML preview" sandbox="allow-scripts" hidden></iframe>
              </section>

              <section class="surface">
                <h3>Verification Logs</h3>
                <div class="split">
                  <div>
                    <label for="red-log">Red Test Output</label>
                    <pre id="red-log" class="log-box">Not available yet.</pre>
                  </div>
                  <div>
                    <label for="verification-log">Verification Output</label>
                    <pre id="verification-log" class="log-box">Not available yet.</pre>
                  </div>
                </div>
              </section>
            </div>
          </div>
        </section>
      </main>
    </div>

    <script type="module">
      const STATE_ORDER = [
        "intake",
        "contract_generation",
        "test_red",
        "implementation_loop",
        "graph_sync",
        "cascade_update",
        "verification",
        "rollback",
        "done",
      ];

      const stateRail = document.getElementById("state-rail");
      const presetSelect = document.getElementById("preset-select");
      const loadPresetButton = document.getElementById("load-preset");
      const clearEditorsButton = document.getElementById("clear-editors");
      const startRunButton = document.getElementById("start-run");
      const fileLoader = document.getElementById("file-loader");
      const statusStrip = document.getElementById("status-strip");
      const eventFeed = document.getElementById("event-feed");
      const metricGrid = document.getElementById("metric-grid");
      const agentList = document.getElementById("agent-list");
      const agentDetail = document.getElementById("agent-detail");
      const cascadePanel = document.getElementById("cascade-panel");
      const graphPanel = document.getElementById("graph-panel");
      const artifactPanel = document.getElementById("artifact-panel");
      const fileList = document.getElementById("file-list");
      const fileMeta = document.getElementById("file-meta");
      const sourceViewButton = document.getElementById("source-view-button");
      const previewViewButton = document.getElementById("preview-view-button");
      const previewHint = document.getElementById("preview-hint");
      const fileViewer = document.getElementById("file-viewer");
      const filePreview = document.getElementById("file-preview");
      const redLog = document.getElementById("red-log");
      const verificationLog = document.getElementById("verification-log");
      const heroState = document.getElementById("hero-state");
      const heroStatus = document.getElementById("hero-status");
      const heroEvents = document.getElementById("hero-events");
      const heroFiles = document.getElementById("hero-files");
      const AGENT_ORDER = ["architect", "qa", "backend", "devops"];

      const editors = {
        openapi: document.getElementById("openapi-input"),
        schema: document.getElementById("schema-input"),
        rules: document.getElementById("rules-input"),
        user_story: document.getElementById("story-input"),
      };

      let currentRunId = null;
      let currentSnapshot = null;
      let pollHandle = null;
      let selectedFilePath = null;
      let selectedAgentRole = null;
      let viewerMode = "source";

      function formatStateLabel(value) {
        return value
          .split("_")
          .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
          .join(" ");
      }

      function escapeHtml(value) {
        return String(value)
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;")
          .replaceAll('"', "&quot;")
          .replaceAll("'", "&#39;");
      }

      function collectVisitedStates(snapshot) {
        const visited = new Set(["intake"]);
        const resultStates = snapshot?.result?.states || [];
        const events = snapshot?.events || [];
        resultStates.forEach((state) => {
          if (state) visited.add(state);
        });
        events.forEach((event) => {
          if (event.state) visited.add(event.state);
        });
        if (snapshot?.current_state) {
          visited.add(snapshot.current_state);
        }
        return visited;
      }

      function collectStatePath(snapshot) {
        const ordered = [];
        const seen = new Set();
        const push = (value) => {
          if (!value || seen.has(value)) return;
          seen.add(value);
          ordered.push(value);
        };
        push("intake");
        (snapshot?.result?.states || []).forEach(push);
        (snapshot?.events || [])
          .filter((event) => event.stage === "state")
          .forEach((event) => push(event.state));
        push(snapshot?.current_state);
        return ordered;
      }

      function renderStateRail(activeState, snapshot) {
        const visited = collectVisitedStates(snapshot);
        stateRail.innerHTML = STATE_ORDER.map((state) => {
          const classes = ["state-pill"];
          if (visited.has(state) && state !== activeState) {
            classes.push(state === "rollback" ? "recovery" : "done");
          }
          if (state === activeState) {
            classes.push("active");
            if (state === "rollback") {
              classes.push("recovery");
            }
          }
          return `<div class="${classes.join(" ")}">${formatStateLabel(state)}</div>`;
        }).join("");
      }

      function setStatus(text, status = "idle") {
        statusStrip.textContent = text;
        statusStrip.dataset.status = status;
      }

      function collectBundleFiles() {
        const files = {};
        if (editors.openapi.value.trim()) files["openapi.yaml"] = editors.openapi.value;
        if (editors.schema.value.trim()) files["schema.sql"] = editors.schema.value;
        if (editors.rules.value.trim()) files["business_rules.yaml"] = editors.rules.value;
        if (editors.user_story.value.trim()) files["user_story.md"] = editors.user_story.value;
        return files;
      }

      function resetEditors() {
        Object.values(editors).forEach((element) => {
          element.value = "";
        });
      }

      function applyPresetFiles(files) {
        resetEditors();
        for (const [name, content] of Object.entries(files)) {
          if (name.startsWith("openapi.")) editors.openapi.value = content;
          if (name === "schema.sql") editors.schema.value = content;
          if (name.includes("rules")) editors.rules.value = content;
          if (name === "user_story.md" || name === "README.md") editors.user_story.value = content;
        }
      }

      async function fetchJson(url, options = {}) {
        const response = await fetch(url, {
          headers: { "Content-Type": "application/json" },
          ...options,
        });
        if (!response.ok) {
          const payload = await response.json().catch(() => ({ detail: response.statusText }));
          throw new Error(payload.detail || response.statusText);
        }
        return response.json();
      }

      function isHtmlPath(path) {
        return Boolean(path && path.toLowerCase().endsWith(".html"));
      }

      function currentFileRecord() {
        return (currentSnapshot?.files || []).find((file) => file.path === selectedFilePath) || null;
      }

      function renderChips(items, emptyText = "No data recorded yet.") {
        if (!items.length) {
          return `<p class="panel-copy muted">${escapeHtml(emptyText)}</p>`;
        }
        return `<div class="chip-list">${items.map((item) => `<span class="chip">${escapeHtml(String(item))}</span>`).join("")}</div>`;
      }

      function renderDetailCard(title, items, emptyText = "No data recorded yet.") {
        return `
          <article class="detail-card">
            <strong>${escapeHtml(title)}</strong>
            ${renderChips(items, emptyText)}
          </article>
        `;
      }

      function renderArtifactValue(value) {
        if (Array.isArray(value)) {
          const simpleArray = value.every((item) => item === null || ["string", "number", "boolean"].includes(typeof item));
          if (simpleArray) {
            return renderChips(value.map((item) => String(item)), "No values recorded.");
          }
          return `<pre class="mini-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        }
        if (value && typeof value === "object") {
          const entries = Object.entries(value);
          const simpleObject = entries.every(([, nestedValue]) => nestedValue === null || ["string", "number", "boolean"].includes(typeof nestedValue));
          if (simpleObject) {
            return `
              <div class="artifact-rows">
                ${entries.map(([nestedKey, nestedValue]) => `
                  <div class="artifact-row">
                    <span>${escapeHtml(nestedKey)}</span>
                    <code>${escapeHtml(String(nestedValue))}</code>
                  </div>
                `).join("")}
              </div>
            `;
          }
          return `<pre class="mini-json">${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
        }
        if (value === null || value === undefined || value === "") {
          return `<p class="panel-copy muted">No values recorded.</p>`;
        }
        return `<code>${escapeHtml(String(value))}</code>`;
      }

      async function loadPresets() {
        const payload = await fetchJson("/api/studio/presets");
        const presets = payload.presets || [];
        presetSelect.innerHTML = presets.map((preset) => {
          const label = preset.display_name || preset.summary || "Example Bundle";
          const detail = preset.summary || preset.service_name || "";
          return `<option value="${preset.name}">${escapeHtml(label)}${detail ? ` · ${escapeHtml(detail)}` : ""}</option>`;
        }).join("");
        if (presets.length) {
          await loadPreset(presets[0].name);
        }
      }

      async function loadPreset(name) {
        const payload = await fetchJson(`/api/studio/presets/${encodeURIComponent(name)}`);
        applyPresetFiles(payload.files || {});
        const bundle = payload.bundle || {};
        const label = payload.display_name || "example specification bundle";
        setStatus(
          bundle.summary
            ? `Loaded ${label}. ${bundle.summary}`
            : `Loaded ${label}.`,
          "idle"
        );
      }

      function renderMetrics(snapshot) {
        const bundle = snapshot?.bundle || {};
        const result = snapshot?.result || {};
        const metrics = [
          ["Bundle", bundle.summary || bundle.service_name || "n/a"],
          ["Current State", formatStateLabel(snapshot?.current_state || "intake")],
          ["Run Status", snapshot?.status || "idle"],
          ["Endpoints", String((bundle.endpoints || []).length)],
          ["Tables", String((bundle.tables || []).length)],
          ["Graph Nodes", result.graph_nodes?.length ? String(result.graph_nodes.length) : "n/a"],
          ["Blast Radius", String((result.blast_radius || []).length)],
          ["Cascade Batches", String((result.cascade_batches || []).length)],
          ["Artifacts", String(Object.keys(result.artifacts || {}).length)],
        ];
        metricGrid.innerHTML = metrics.map(([label, value]) => `
          <div class="metric">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(String(value))}</span>
          </div>
        `).join("");
      }

      function formatRoleLabel(role) {
        return {
          architect: "Architect",
          qa: "QA",
          backend: "Backend",
          devops: "DevOps",
        }[role] || formatStateLabel(role);
      }

      function parseTimestampMs(value) {
        const parsed = Date.parse(value || "");
        return Number.isFinite(parsed) ? parsed : null;
      }

      function formatDuration(ms) {
        if (!ms) return "n/a";
        if (ms < 1000) return `${ms} ms`;
        const seconds = ms / 1000;
        if (seconds < 60) {
          return `${seconds >= 10 ? seconds.toFixed(0) : seconds.toFixed(1)} s`;
        }
        const minutes = Math.floor(seconds / 60);
        const remainderSeconds = Math.round(seconds % 60);
        return `${minutes}m ${remainderSeconds}s`;
      }

      function collectAgentRecords(snapshot) {
        const artifacts = snapshot?.result?.artifacts || {};
        const events = snapshot?.events || [];
        return AGENT_ORDER.map((role) => {
          const roleEvents = events.filter((event) => event.role === role);
          const artifact = artifacts[role] || null;
          if (!roleEvents.length && !artifact) {
            return null;
          }

          let activeStartEvent = null;
          let totalDurationMs = 0;
          const phaseRuns = [];
          roleEvents.forEach((event) => {
            const timestampMs = parseTimestampMs(event.timestamp);
            if (event.status === "started") {
              activeStartEvent = event;
            }
            if (event.status === "completed" && activeStartEvent) {
              const startedMs = parseTimestampMs(activeStartEvent.timestamp);
              const durationMs =
                startedMs !== null && timestampMs !== null
                  ? Math.max(0, timestampMs - startedMs)
                  : null;
              if (durationMs !== null) {
                totalDurationMs += durationMs;
              }
              phaseRuns.push({
                state: event.state || activeStartEvent.state || "unknown",
                batch: event.batch ?? activeStartEvent.batch ?? null,
                started_at: activeStartEvent.timestamp,
                completed_at: event.timestamp,
                duration_ms: durationMs,
                patch_count: Number(event.details?.patch_count || 0),
                file_paths: Array.isArray(event.details?.file_paths) ? event.details.file_paths : [],
                generation_mode: event.details?.generation_mode || null,
                message: event.message,
              });
              activeStartEvent = null;
            }
          });

          const lastEvent = roleEvents[roleEvents.length - 1] || null;
          const latestStarted = [...roleEvents].reverse().find((event) => event.status === "started") || null;
          const latestCompleted = [...roleEvents].reverse().find((event) => event.status === "completed") || null;
          const latestMessage = latestCompleted?.message || lastEvent?.message || "No agent summary recorded yet.";
          const latestContext = Array.isArray(latestStarted?.details?.context_node_ids)
            ? latestStarted.details.context_node_ids
            : [];
          const eventFilePaths = roleEvents.flatMap((event) =>
            Array.isArray(event.details?.file_paths) ? event.details.file_paths : []
          );
          const inferredFilePaths = [];
          if (typeof artifact?.test_file === "string") {
            inferredFilePaths.push(artifact.test_file);
          }
          if (typeof artifact?.service === "string" && artifact.service.includes("::")) {
            inferredFilePaths.push(artifact.service.split("::", 1)[0]);
          }
          if (role === "devops") {
            inferredFilePaths.push("Dockerfile", "docker-compose.yml", ".dockerignore");
          }
          const filePaths = [...new Set([...eventFilePaths, ...inferredFilePaths])];
          const artifactAttempts = Number.parseInt(String(artifact?.llm_attempts || ""), 10);
          const attemptRecords = Array.isArray(artifact?.llm_attempt_records) ? artifact.llm_attempt_records : [];
          const patchDiffs = Array.isArray(latestCompleted?.details?.patch_diffs)
            ? latestCompleted.details.patch_diffs
            : [];
          return {
            role,
            label: formatRoleLabel(role),
            artifact: artifact || {},
            events: roleEvents,
            lastEvent,
            latestStarted,
            latestCompleted,
            latestMessage,
            latestContext,
            filePaths,
            status: latestCompleted ? "completed" : latestStarted ? "running" : "idle",
            patchCount: Number(latestCompleted?.details?.patch_count || 0),
            generationMode: latestCompleted?.details?.generation_mode || artifact?.generation_mode || null,
            durationMs: totalDurationMs || null,
            attempts: Number.isFinite(artifactAttempts) ? artifactAttempts : null,
            attemptRecords,
            phaseRuns,
            patchDiffs,
          };
        }).filter(Boolean);
      }

      function ensureSelectedAgent(records) {
        if (!records.length) {
          selectedAgentRole = null;
          return null;
        }
        if (selectedAgentRole && records.some((record) => record.role === selectedAgentRole)) {
          return records.find((record) => record.role === selectedAgentRole) || records[0];
        }
        const latestRecord = [...records].sort((left, right) => {
          const leftMs = parseTimestampMs(left.lastEvent?.timestamp) || 0;
          const rightMs = parseTimestampMs(right.lastEvent?.timestamp) || 0;
          return rightMs - leftMs;
        })[0] || records[0];
        selectedAgentRole = latestRecord.role;
        return latestRecord;
      }

      function syncSelectedFileForAgent(record, snapshot) {
        if (!record) return;
        const availablePaths = new Set((snapshot?.files || []).map((file) => file.path));
        const candidatePaths = record.filePaths.filter((path) => availablePaths.has(path));
        if (!candidatePaths.length) return;
        if (!selectedFilePath || !candidatePaths.includes(selectedFilePath)) {
          selectedFilePath = candidatePaths[0];
        }
      }

      function renderAgentTrace(snapshot) {
        const records = collectAgentRecords(snapshot);
        if (!records.length) {
          agentList.innerHTML = `
            <p class="panel-copy muted">
              Start a run to inspect each agent, its latest status, and the files it emitted.
            </p>
          `;
          agentDetail.innerHTML = `
            <p class="panel-copy muted">
              Select an agent to inspect its status, context, outputs, and related run events.
            </p>
          `;
          return;
        }

        const selectedRecord = ensureSelectedAgent(records);
        syncSelectedFileForAgent(selectedRecord, snapshot);

        agentList.innerHTML = records.map((record) => `
          <button
            class="agent-button ${record.role === selectedAgentRole ? "is-active" : ""}"
            type="button"
            data-role="${escapeHtml(record.role)}"
          >
            <div class="agent-button__top">
              <strong class="agent-button__name">${escapeHtml(record.label)}</strong>
              <span class="tag" data-tone="${toneForEvent({ status: record.status === "completed" ? "completed" : "active" })}">
                ${escapeHtml(record.status)}
              </span>
            </div>
            <div class="agent-button__meta">
              ${record.generationMode ? `<span class="tag">${escapeHtml(record.generationMode)}</span>` : ""}
              ${record.attempts ? `<span class="tag">${escapeHtml(String(record.attempts))} attempt${record.attempts === 1 ? "" : "s"}</span>` : ""}
              ${record.filePaths.length ? `<span class="tag">${escapeHtml(String(record.filePaths.length))} files</span>` : ""}
            </div>
            <p class="agent-button__summary">${escapeHtml(record.latestMessage)}</p>
          </button>
        `).join("");

        Array.from(agentList.querySelectorAll("button[data-role]")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedAgentRole = button.dataset.role;
            const activeRecord = records.find((record) => record.role === selectedAgentRole) || null;
            syncSelectedFileForAgent(activeRecord, currentSnapshot);
            renderAgentTrace(currentSnapshot);
            await renderFiles(currentSnapshot);
          });
        });

        const visibleRecord = selectedRecord || records[0];
        agentDetail.innerHTML = `
          <article class="agent-section">
            <div class="agent-detail__header">
              <div>
                <h4 class="agent-detail__title">${escapeHtml(visibleRecord.label)}</h4>
                <p class="agent-detail__copy">${escapeHtml(visibleRecord.latestMessage)}</p>
              </div>
              <div class="agent-button__meta">
                <span class="tag" data-tone="${toneForEvent({ status: visibleRecord.status === "completed" ? "completed" : "active" })}">
                  ${escapeHtml(visibleRecord.status)}
                </span>
                ${visibleRecord.generationMode ? `<span class="tag">${escapeHtml(visibleRecord.generationMode)}</span>` : ""}
                ${visibleRecord.artifact?.llm_model ? `<span class="tag">${escapeHtml(String(visibleRecord.artifact.llm_model))}</span>` : ""}
              </div>
            </div>
          </article>
          <div class="summary-grid">
            <article class="summary-card">
              <strong>Events</strong>
              <span>${escapeHtml(String(visibleRecord.events.length))}</span>
            </article>
            <article class="summary-card">
              <strong>Duration</strong>
              <span>${escapeHtml(formatDuration(visibleRecord.durationMs))}</span>
            </article>
            <article class="summary-card">
              <strong>Patches</strong>
              <span>${escapeHtml(String(visibleRecord.patchCount))}</span>
            </article>
          </div>
          <article class="agent-section">
            <strong>Context Slice</strong>
            ${renderChips(visibleRecord.latestContext || [], "No context nodes were recorded for this agent run.")}
          </article>
          <article class="agent-section">
            <strong>Phase Breakdown</strong>
            ${
              visibleRecord.phaseRuns.length
                ? `<div class="agent-event-list">${visibleRecord.phaseRuns.map((run) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(formatStateLabel(run.state || "unknown"))}</span>
                        ${run.batch ? `<span class="tag">batch ${run.batch}</span>` : ""}
                        ${run.generation_mode ? `<span class="tag">${escapeHtml(String(run.generation_mode))}</span>` : ""}
                        <span class="tag">${escapeHtml(formatDuration(run.duration_ms))}</span>
                      </div>
                      <p>${escapeHtml(run.message || "Completed phase run.")}</p>
                    </div>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">No completed phase runs have been recorded for this agent yet.</p>`
            }
          </article>
          <article class="agent-section">
            <strong>Retry History</strong>
            ${
              visibleRecord.attemptRecords.length
                ? `<div class="agent-event-list">${visibleRecord.attemptRecords.map((attempt) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(`Attempt ${attempt.attempt ?? "?"}`)}</span>
                        <span class="tag" data-tone="${attempt.success ? "success" : "active"}">${escapeHtml(attempt.success ? "success" : "failed")}</span>
                        ${attempt.kind ? `<span class="tag">${escapeHtml(String(attempt.kind))}</span>` : ""}
                        ${attempt.model ? `<span class="tag">${escapeHtml(String(attempt.model))}</span>` : ""}
                        ${attempt.cached ? `<span class="tag">cached</span>` : ""}
                      </div>
                      <p>${escapeHtml(String(attempt.error || attempt.response_format || "Attempt completed without additional notes."))}</p>
                    </div>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">This agent did not record independent retry attempts for the current run.</p>`
            }
          </article>
          <article class="agent-section">
            <strong>Related Files</strong>
            ${
              visibleRecord.filePaths.length
                ? `<div class="file-chip-row">${visibleRecord.filePaths.map((path) => `
                    <button
                      class="file-chip-button ${selectedFilePath === path ? "is-active" : ""}"
                      type="button"
                      data-path="${escapeHtml(path)}"
                    >
                      ${escapeHtml(path)}
                    </button>
                  `).join("")}</div>`
                : `<p class="panel-copy muted">This agent did not emit workspace files for the current run.</p>`
            }
          </article>
          <article class="agent-section">
            <strong>Artifact Snapshot</strong>
            ${renderArtifactValue(visibleRecord.artifact)}
          </article>
          <article class="agent-section">
            <strong>Patch Diffs</strong>
            ${
              visibleRecord.patchDiffs.length
                ? visibleRecord.patchDiffs.map((patch) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag">${escapeHtml(String(patch.operation || "patch"))}</span>
                        <span class="tag">${escapeHtml(String(patch.path || "unknown"))}</span>
                      </div>
                      <pre class="mini-json">${escapeHtml(String(patch.diff || ""))}</pre>
                    </div>
                  `).join("")
                : `<p class="panel-copy muted">No patch diffs were recorded for this agent run.</p>`
            }
          </article>
          <article class="agent-section">
            <strong>Related Events</strong>
            <div class="agent-event-list">
              ${visibleRecord.events.length
                ? visibleRecord.events.slice().reverse().map((event) => `
                    <div class="agent-event-card">
                      <div class="event-meta">
                        <span class="tag" data-tone="${toneForEvent(event)}">${escapeHtml(event.stage)}</span>
                        <span class="tag">${escapeHtml(event.status)}</span>
                        ${event.batch ? `<span class="tag">batch ${event.batch}</span>` : ""}
                      </div>
                      <p>${escapeHtml(event.message)}</p>
                    </div>
                  `).join("")
                : `<p class="panel-copy muted">No agent events recorded yet.</p>`}
            </div>
          </article>
        `;

        Array.from(agentDetail.querySelectorAll("button[data-path]")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedFilePath = button.dataset.path;
            viewerMode = "source";
            renderAgentTrace(currentSnapshot);
            await renderFiles(currentSnapshot);
          });
        });
      }

      function renderCascade(snapshot) {
        const result = snapshot?.result || {};
        const batches = result.cascade_batches || [];
        const tasks = result.cascade_tasks || [];
        const taskMap = new Map(tasks.map((task) => [task.node_id, task]));
        if (!batches.length && !tasks.length) {
          cascadePanel.innerHTML = `
            <p class="panel-copy muted">
              Cascade planning data will appear here once the run reaches graph-aware repair and impact propagation.
            </p>
          `;
          return;
        }
        const cyclicCount = tasks.filter((task) => task.cyclic).length;
        cascadePanel.innerHTML = `
          <div class="summary-grid">
            <article class="summary-card">
              <strong>Impacted Nodes</strong>
              <span>${escapeHtml(String((result.blast_radius || []).length))}</span>
            </article>
            <article class="summary-card">
              <strong>Batch Count</strong>
              <span>${escapeHtml(String(batches.length))}</span>
            </article>
            <article class="summary-card">
              <strong>Cyclic Tasks</strong>
              <span>${escapeHtml(String(cyclicCount))}</span>
            </article>
          </div>
          ${renderDetailCard("Execution Order", result.cascade_order || [], "Execution order is not available yet.")}
          <div class="batch-stack">
            ${batches.map((batch, index) => `
              <article class="batch-card">
                <header>
                  <h4>Batch ${index + 1}</h4>
                  <span class="tag">${escapeHtml(String(batch.length))} nodes</span>
                </header>
                <div class="batch-node-list">
                  ${batch.map((nodeId) => {
                    const task = taskMap.get(nodeId) || {};
                    const dependencyCount = Array.isArray(task.dependencies) ? task.dependencies.length : 0;
                    const contextCount = Array.isArray(task.context_node_ids) ? task.context_node_ids.length : 0;
                    return `
                      <div class="batch-node">
                        <code>${escapeHtml(nodeId)}</code>
                        <div class="batch-meta">
                          ${task.role ? `<span class="tag">${escapeHtml(task.role)}</span>` : ""}
                          <span class="tag">${dependencyCount ? `${dependencyCount} deps` : "root"}</span>
                          ${contextCount ? `<span class="tag">${contextCount} context</span>` : ""}
                          ${task.cyclic ? `<span class="tag">cyclic</span>` : ""}
                        </div>
                      </div>
                    `;
                  }).join("")}
                </div>
              </article>
            `).join("")}
          </div>
        `;
      }

      function renderGraph(snapshot) {
        const result = snapshot?.result || {};
        const statePath = collectStatePath(snapshot);
        const graphNodes = result.graph_nodes || [];
        const added = result.graph_sync_added || [];
        const removed = result.graph_sync_removed || [];
        const hasGraphData = statePath.length > 1 || graphNodes.length || added.length || removed.length;
        if (!hasGraphData) {
          graphPanel.innerHTML = `
            <p class="panel-copy muted">
              State path, graph sync deltas, and the focused context slice will populate as the run advances.
            </p>
          `;
          return;
        }
        graphPanel.innerHTML = `
          <div class="summary-grid">
            <article class="summary-card">
              <strong>State Steps</strong>
              <span>${escapeHtml(String(statePath.length))}</span>
            </article>
            <article class="summary-card">
              <strong>Added Nodes</strong>
              <span>${escapeHtml(String(added.length))}</span>
            </article>
            <article class="summary-card">
              <strong>Removed Nodes</strong>
              <span>${escapeHtml(String(removed.length))}</span>
            </article>
          </div>
          <div class="detail-list">
            ${renderDetailCard("State Path", statePath.map((state) => formatStateLabel(state)), "No state transitions recorded yet.")}
            ${renderDetailCard("Blast Radius", result.blast_radius || [], "Blast radius not available yet.")}
            ${renderDetailCard("Context Slice", result.context_slice_nodes || [], "Context slice not available yet.")}
            ${renderDetailCard("Graph Sync Added", added, "No nodes were added during sync.")}
            ${renderDetailCard("Graph Sync Removed", removed, "No nodes were removed during sync.")}
          </div>
        `;
      }

      function renderArtifacts(snapshot) {
        const artifacts = snapshot?.result?.artifacts || {};
        const entries = Object.entries(artifacts);
        if (!entries.length) {
          artifactPanel.innerHTML = `
            <p class="panel-copy muted">
              Artifact summaries will appear once the agents emit routes, components, files, and risk notes.
            </p>
          `;
          return;
        }
        artifactPanel.innerHTML = entries.map(([role, artifact]) => `
          <article class="artifact-card">
            <header>
              <h4>${escapeHtml(role)}</h4>
              <span class="tag">${escapeHtml(String(Object.keys(artifact || {}).length))} fields</span>
            </header>
            <div class="artifact-grid">
              ${Object.entries(artifact || {}).map(([key, value]) => `
                <section class="artifact-group">
                  <strong>${escapeHtml(key)}</strong>
                  ${renderArtifactValue(value)}
                </section>
              `).join("")}
            </div>
          </article>
        `).join("");
      }

      function toneForEvent(event) {
        if (event.status === "failed" || event.status === "unexpected_pass") return "active";
        if (event.status === "completed" || event.status === "expected_failure") return "success";
        return "active";
      }

      function renderEvents(snapshot) {
        const events = snapshot?.events || [];
        heroEvents.textContent = String(events.length);
        if (!events.length) {
          eventFeed.innerHTML = `
            <div class="event-card">
              <div class="event-meta"><span class="tag" data-tone="active">idle</span></div>
              <strong>No run activity yet</strong>
              <p>Once the generation pipeline starts, this feed will show state changes, agent summaries, graph sync work, and final verification.</p>
            </div>
          `;
          return;
        }
        eventFeed.innerHTML = events.slice().reverse().map((event) => {
          const meta = [
            `<span class="tag" data-tone="${toneForEvent(event)}">${escapeHtml(event.stage)}</span>`,
            event.role ? `<span class="tag">${escapeHtml(event.role)}</span>` : "",
            event.batch ? `<span class="tag">batch ${event.batch}</span>` : "",
            event.state ? `<span class="tag">${escapeHtml(formatStateLabel(event.state))}</span>` : "",
          ].join("");
          const detailEntries = Object.entries(event.details || {});
          const detailCopy = detailEntries.length
            ? `<div class="batch-meta">${detailEntries.slice(0, 3).map(([key, value]) => `<span class="tag">${escapeHtml(key)}: ${escapeHtml(Array.isArray(value) ? String(value.length) : String(value))}</span>`).join("")}</div>`
            : "";
          return `
            <article class="event-card">
              <div class="event-meta">${meta}</div>
              <strong>${escapeHtml(event.message)}</strong>
              ${detailCopy}
              <p>${escapeHtml(new Date(event.timestamp).toLocaleString())}</p>
            </article>
          `;
        }).join("");
      }

      function syncFileViewerMode() {
        const previewEnabled = isHtmlPath(selectedFilePath);
        if (!previewEnabled && viewerMode === "preview") {
          viewerMode = "source";
        }
        sourceViewButton.classList.toggle("is-active", viewerMode === "source");
        previewViewButton.classList.toggle("is-active", viewerMode === "preview");
        previewViewButton.hidden = !previewEnabled;
        fileViewer.hidden = viewerMode !== "source";
        filePreview.hidden = viewerMode !== "preview" || !previewEnabled;
        if (!previewEnabled) {
          filePreview.removeAttribute("src");
        }
        const file = currentFileRecord();
        fileMeta.textContent = file
          ? `${file.path} · ${file.language} · ${file.size} bytes`
          : "No generated file selected yet.";
        if (!selectedFilePath) {
          previewHint.textContent = "Source is available for every generated file. Preview only appears for generated HTML files.";
          return;
        }
        previewHint.textContent = previewEnabled
          ? "Switch between raw source and a live render of the generated HTML output for this run."
          : "Preview is unavailable for this file type. Source view remains active for non-HTML outputs.";
      }

      function updatePreviewFrame() {
        if (!currentRunId || !selectedFilePath || !isHtmlPath(selectedFilePath)) {
          filePreview.removeAttribute("src");
          return;
        }
        filePreview.src = `/api/studio/runs/${currentRunId}/preview?path=${encodeURIComponent(selectedFilePath)}&updated_at=${encodeURIComponent(currentSnapshot?.updated_at || "")}`;
      }

      async function renderFiles(snapshot) {
        const files = snapshot?.files || [];
        heroFiles.textContent = String(files.length);
        if (!files.length) {
          fileList.innerHTML = "";
          fileViewer.textContent = "No generated files available yet.";
          selectedFilePath = null;
          syncFileViewerMode();
          return;
        }
        if (!selectedFilePath || !files.some((file) => file.path === selectedFilePath)) {
          selectedFilePath = files[0].path;
        }
        fileList.innerHTML = files.map((file) => `
          <button class="${file.path === selectedFilePath ? "active" : ""}" data-path="${escapeHtml(file.path)}">
            ${escapeHtml(file.path)}
          </button>
        `).join("");
        Array.from(fileList.querySelectorAll("button")).forEach((button) => {
          button.addEventListener("click", async () => {
            selectedFilePath = button.dataset.path;
            await renderFiles(currentSnapshot);
          });
        });
        await loadSelectedFile();
      }

      async function loadSelectedFile() {
        syncFileViewerMode();
        updatePreviewFrame();
        if (!currentRunId || !selectedFilePath) return;
        const response = await fetch(`/api/studio/runs/${currentRunId}/file?path=${encodeURIComponent(selectedFilePath)}`);
        if (!response.ok) {
          fileViewer.textContent = "Unable to load selected file.";
          return;
        }
        fileViewer.textContent = await response.text();
      }

      function renderLogs(snapshot) {
        const result = snapshot?.result || {};
        redLog.textContent = result.red_test_output || snapshot?.error || "Not available yet.";
        verificationLog.textContent = result.verification_output || snapshot?.error || "Not available yet.";
      }

      async function renderSnapshot(snapshot) {
        currentSnapshot = snapshot;
        heroState.textContent = formatStateLabel(snapshot.current_state || "intake");
        heroStatus.textContent = (snapshot.status || "idle").toUpperCase();
        setStatus(snapshot.current_message || "Waiting for next action.", snapshot.status || "idle");
        renderStateRail(snapshot.current_state || "intake", snapshot);
        renderMetrics(snapshot);
        renderAgentTrace(snapshot);
        renderCascade(snapshot);
        renderGraph(snapshot);
        renderArtifacts(snapshot);
        renderEvents(snapshot);
        renderLogs(snapshot);
        await renderFiles(snapshot);
      }

      function stopPolling() {
        if (pollHandle) {
          clearTimeout(pollHandle);
          pollHandle = null;
        }
      }

      async function pollRun() {
        if (!currentRunId) return;
        const snapshot = await fetchJson(`/api/studio/runs/${currentRunId}`);
        await renderSnapshot(snapshot);
        if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
          pollHandle = setTimeout(pollRun, 1100);
        }
      }

      async function startRun() {
        stopPolling();
        selectedFilePath = null;
        selectedAgentRole = null;
        viewerMode = "source";
        startRunButton.disabled = true;
        setStatus("Creating a fresh studio run and preparing the generation workspace.", "running");
        try {
          const snapshot = await fetchJson("/api/studio/runs", {
            method: "POST",
            body: JSON.stringify({ files: collectBundleFiles() }),
          });
          currentRunId = snapshot.run_id;
          await renderSnapshot(snapshot);
          if (!TERMINAL_RUN_STATES.has(snapshot.status)) {
            pollHandle = setTimeout(pollRun, 700);
          }
        } catch (error) {
          setStatus(error.message, "failed");
        } finally {
          startRunButton.disabled = false;
        }
      }

      async function importFiles(fileListLike) {
        const files = Array.from(fileListLike || []);
        if (!files.length) return;
        const mapped = {};
        for (const file of files) {
          mapped[file.name] = await file.text();
        }
        applyPresetFiles(mapped);
        setStatus("Imported local structured files into the editors. Review them, then run the generation pipeline.", "idle");
      }

      const TERMINAL_RUN_STATES = new Set(["succeeded", "failed"]);

      loadPresetButton.addEventListener("click", async () => {
        if (!presetSelect.value) return;
        await loadPreset(presetSelect.value);
      });

      clearEditorsButton.addEventListener("click", () => {
        resetEditors();
        setStatus("Editors cleared. Import files or load an example bundle to continue.", "idle");
      });

      startRunButton.addEventListener("click", startRun);

      sourceViewButton.addEventListener("click", async () => {
        viewerMode = "source";
        syncFileViewerMode();
        await loadSelectedFile();
      });

      previewViewButton.addEventListener("click", () => {
        if (!isHtmlPath(selectedFilePath)) {
          return;
        }
        viewerMode = "preview";
        syncFileViewerMode();
        updatePreviewFrame();
      });

      fileLoader.addEventListener("change", async (event) => {
        await importFiles(event.target.files);
        event.target.value = "";
      });

      syncFileViewerMode();
      renderStateRail("intake", null);
      loadPresets().catch((error) => {
        setStatus(error.message, "failed");
      });
    </script>
  </body>
</html>
"""
