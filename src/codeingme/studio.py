"""定义 Studio 演示界面与后端接口。"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from typing import Callable
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .env import load_project_dotenv
from .orchestrator import CodeingmeOrchestrator, OrchestrationEvent
from .orchestrator.state_machine import ExecutionState
from .run_paths import create_run_root
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


load_project_dotenv()


class StudioRunRequest(BaseModel):
    preset_name: str | None = None
    files: dict[str, str] = Field(default_factory=dict)


@dataclass(slots=True)
class StudioRunRecord:
    run_id: str
    source: str
    case_name: str
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
            "source": self.source,
            "case_name": self.case_name,
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
        self.run_root = self.repo_root / ".codeingme" / "runs" / "studio"
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.orchestrator_factory = orchestrator_factory or (
            lambda workspace_root: CodeingmeOrchestrator(workspace_root=workspace_root)
        )
        self.run_inline = run_inline
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="codeingme-studio")
        self._runs: dict[str, StudioRunRecord] = {}
        self._lock = threading.Lock()
        self._load_runs_from_disk()

    def list_runs(self) -> list[dict[str, object]]:
        with self._lock:
            records = sorted(
                self._runs.values(),
                key=lambda record: record.updated_at,
                reverse=True,
            )
            return [self._run_summary(record) for record in records]

    def list_presets(self) -> list[dict[str, object]]:
        if not self.specs_root.exists():
            return []
        presets: list[dict[str, object]] = []
        for spec_dir in sorted(path for path in self.specs_root.iterdir() if path.is_dir()):
            try:
                bundle = load_spec_bundle(spec_dir)
            except FileNotFoundError:
                continue
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
        case_name = payload.preset_name or "custom"
        run_root, run_id = create_run_root(self.repo_root, source="studio", case_name=case_name)
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
            source="studio",
            case_name=case_name,
            status="queued",
            created_at=_utc_now(),
            updated_at=_utc_now(),
            current_state="intake",
            current_message="规格包已接收，等待生成流程开始。",
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
            self._persist_record(record)

        if self.run_inline:
            self._execute_run(run_id)
        else:
            self._executor.submit(self._execute_run, run_id)
        return self.get_run(run_id)

    def resume_run(self, run_id: str) -> dict[str, object]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            resume_from_state = self._resume_from_state(record)
            if record.status != "failed" or resume_from_state is None:
                raise ValueError("Only failed runs with resumable state can be resumed.")
            record.status = "queued"
            record.updated_at = _utc_now()
            record.current_message = f"准备从 {resume_from_state} 继续运行。"
            record.error = None
            record.events.append(
                {
                    "sequence": len(record.events) + 1,
                    "timestamp": _utc_now(),
                    "stage": "run",
                    "status": "queued",
                    "message": f"Queued a resume request from {resume_from_state}.",
                    "state": record.current_state,
                    "role": None,
                    "batch": None,
                    "details": {"resume_from": resume_from_state},
                }
            )
            self._persist_record(record)

        if self.run_inline:
            self._execute_resume(run_id)
        else:
            self._executor.submit(self._execute_resume, run_id)
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, object]:
        with self._lock:
            record = self._runs.get(run_id)
            if record is None:
                raise KeyError(run_id)
            return self._run_payload(record)

    def read_run_file(self, run_id: str, relative_path: str) -> str:
        _, candidate = self._resolve_run_file(run_id, relative_path)
        if not candidate.exists() or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate.read_text(encoding="utf-8")

    def _execute_run(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            record.status = "running"
            record.updated_at = _utc_now()
            record.error = None
            self._persist_record(record)
        self._append_manual_event(
            record,
            stage="run",
            status="started",
            message="Studio launched a dedicated generation workspace for this run.",
            state="intake",
        )
        self._persist_record(record)

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
                self._persist_record(current)

        try:
            orchestrator = self.orchestrator_factory(record.workspace_root)
            result = orchestrator.run(record.requirement, event_callback=on_event)
            files = self._collect_workspace_files(record.workspace_root)
            with self._lock:
                current = self._runs[run_id]
                current.status = "succeeded"
                current.updated_at = _utc_now()
                current.current_state = result.final_state
                current.current_message = "运行已成功完成。"
                current.result = asdict(result)
                current.files = files
                current.error = None
                self._persist_record(current)
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
                self._persist_record(current)

    def _execute_resume(self, run_id: str) -> None:
        with self._lock:
            record = self._runs[run_id]
            resume_from_state = self._resume_from_state(record)
            if resume_from_state is None:
                raise ValueError("Resume state is unavailable for this run.")
            resume_payload = self._resume_payload(record)
            record.status = "running"
            record.updated_at = _utc_now()
            record.current_message = f"正在从 {resume_from_state} 继续运行。"
            record.error = None
            self._persist_record(record)

        self._append_manual_event(
            record,
            stage="run",
            status="started",
            message=f"Studio is resuming this run from {resume_from_state}.",
            state=record.current_state,
        )
        self._persist_record(record)

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
                self._persist_record(current)

        try:
            orchestrator = self.orchestrator_factory(record.workspace_root)
            if resume_from_state == ExecutionState.CONTRACT_GENERATION.value:
                result = orchestrator.run(record.requirement, event_callback=on_event)
            else:
                result = orchestrator.resume(
                    record.requirement,
                    resume_from=resume_from_state,
                    schemas_data=resume_payload["schemas_data"],
                    apis_data=resume_payload["apis_data"],
                    generated_tests_data=resume_payload["generated_tests_data"],
                    prior_artifacts=resume_payload["artifacts"],
                    red_test_output=resume_payload.get("red_test_output", ""),
                    event_callback=on_event,
                )
            files = self._collect_workspace_files(record.workspace_root)
            with self._lock:
                current = self._runs[run_id]
                current.status = "succeeded"
                current.updated_at = _utc_now()
                current.current_state = result.final_state
                current.current_message = "运行已成功完成。"
                current.result = asdict(result)
                current.files = files
                current.error = None
                self._persist_record(current)
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
                        "details": {"resume": True},
                    }
                )
                self._persist_record(current)

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

    def _record_storage_payload(self, record: StudioRunRecord) -> dict[str, object]:
        return {
            **record.to_dict(),
            "source": record.source,
            "case_name": record.case_name,
            "run_root": str(record.run_root),
            "spec_dir": str(record.spec_dir),
        }

    def _persist_record(self, record: StudioRunRecord) -> None:
        payload = self._record_storage_payload(record)
        target = record.run_root / "run.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _load_runs_from_disk(self) -> None:
        if not self.run_root.exists():
            return
        for payload_path in sorted(self.run_root.rglob("run.json")):
            run_dir = payload_path.parent
            if not payload_path.exists():
                continue
            try:
                payload = json.loads(payload_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            try:
                record = StudioRunRecord(
                    run_id=payload["run_id"],
                    source=payload.get("source", "studio"),
                    case_name=payload.get("case_name", run_dir.parent.name if run_dir.parent != self.run_root else "custom"),
                    status=payload["status"],
                    created_at=payload["created_at"],
                    updated_at=payload["updated_at"],
                    current_state=payload["current_state"],
                    current_message=payload["current_message"],
                    requirement=payload["requirement"],
                    bundle=dict(payload.get("bundle", {})),
                    run_root=Path(payload.get("run_root", run_dir)),
                    workspace_root=Path(payload.get("workspace_root", run_dir / "workspace")),
                    spec_dir=Path(payload.get("spec_dir", run_dir / "spec_bundle")),
                    events=list(payload.get("events", [])),
                    result=payload.get("result"),
                    error=payload.get("error"),
                    files=list(payload.get("files", [])),
                )
            except KeyError:
                continue
            self._runs[record.run_id] = record

    def _run_payload(self, record: StudioRunRecord) -> dict[str, object]:
        resume_from_state = self._resume_from_state(record)
        payload = record.to_dict()
        payload["resume_supported"] = record.status == "failed" and resume_from_state is not None
        payload["resume_from_state"] = resume_from_state
        return payload

    def _run_summary(self, record: StudioRunRecord) -> dict[str, object]:
        resume_from_state = self._resume_from_state(record)
        return {
            "run_id": record.run_id,
            "source": record.source,
            "case_name": record.case_name,
            "status": record.status,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "current_state": record.current_state,
            "current_message": record.current_message,
            "requirement": record.requirement,
            "bundle": copy.deepcopy(record.bundle),
            "error": record.error,
            "file_count": len(record.files),
            "resume_supported": record.status == "failed" and resume_from_state is not None,
            "resume_from_state": resume_from_state,
        }

    def _resume_from_state(self, record: StudioRunRecord) -> str | None:
        mapping = {
            ExecutionState.CONTRACT_GENERATION.value: ExecutionState.CONTRACT_GENERATION.value,
            ExecutionState.TEST_RED.value: ExecutionState.TEST_RED.value,
            ExecutionState.IMPLEMENTATION_LOOP.value: ExecutionState.IMPLEMENTATION_LOOP.value,
            ExecutionState.GRAPH_SYNC.value: ExecutionState.IMPLEMENTATION_LOOP.value,
            ExecutionState.CASCADE_UPDATE.value: ExecutionState.IMPLEMENTATION_LOOP.value,
            ExecutionState.VERIFICATION.value: ExecutionState.IMPLEMENTATION_LOOP.value,
            ExecutionState.ROLLBACK.value: ExecutionState.IMPLEMENTATION_LOOP.value,
        }
        candidate = mapping.get(record.current_state)
        if candidate is None:
            return None
        payload = self._resume_payload(record)
        if candidate == ExecutionState.CONTRACT_GENERATION.value:
            return candidate
        if candidate == ExecutionState.TEST_RED.value and payload["schemas_data"] and payload["apis_data"]:
            return candidate
        if (
            candidate == ExecutionState.IMPLEMENTATION_LOOP.value
            and payload["schemas_data"]
            and payload["apis_data"]
            and payload["generated_tests_data"]
        ):
            return candidate
        return None

    def _resume_payload(self, record: StudioRunRecord) -> dict[str, object]:
        schemas_data: list[dict[str, object]] = []
        apis_data: list[dict[str, object]] = []
        generated_tests_data: list[dict[str, object]] = []
        artifacts: dict[str, dict[str, object]] = {}

        for event in record.events:
            details = event.get("details", {})
            if event.get("stage") == "contracts" and event.get("status") == "completed":
                schemas_data = copy.deepcopy(details.get("schemas_data", []))
                apis_data = copy.deepcopy(details.get("apis_data", []))
            if event.get("stage") == "agent" and event.get("status") == "completed":
                role = event.get("role")
                artifact = details.get("artifact")
                if role and isinstance(artifact, dict):
                    artifacts[role] = copy.deepcopy(artifact)
                if role == "qa":
                    generated_tests_data = copy.deepcopy(details.get("tests", []))

        if record.result and isinstance(record.result.get("artifacts"), dict):
            for role, artifact in record.result["artifacts"].items():
                if isinstance(role, str) and isinstance(artifact, dict):
                    artifacts[role] = copy.deepcopy(artifact)

        return {
            "schemas_data": schemas_data,
            "apis_data": apis_data,
            "generated_tests_data": generated_tests_data,
            "artifacts": artifacts,
            "red_test_output": record.result.get("red_test_output", "") if record.result else "",
        }


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
    
    ui_dir = Path(__file__).parent / "ui"
    app.mount("/assets", StaticFiles(directory=str(ui_dir)), name="assets")


    @app.get("/", response_class=HTMLResponse)
    def studio_home() -> HTMLResponse:
        return FileResponse(Path(__file__).parent / "ui" / "index.html")

    @app.get("/api/studio/presets")
    def list_presets() -> dict[str, object]:
        return {"presets": manager.list_presets()}

    @app.get("/api/studio/runs")
    def list_runs() -> dict[str, object]:
        return {"runs": manager.list_runs()}

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

    @app.post("/api/studio/runs/{run_id}/resume")
    def resume_run(run_id: str) -> dict[str, object]:
        try:
            return manager.resume_run(run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}") from exc
        except ValueError as exc:
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
