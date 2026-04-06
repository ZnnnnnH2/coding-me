from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
import sys


@dataclass(slots=True)
class CommandResult:
    command: str
    success: bool
    output: str
    returncode: int


@dataclass(slots=True)
class ContainerTestConfig:
    compose_file: str = "docker-compose.yml"
    service: str = "test"
    pytest_command: tuple[str, ...] = field(default_factory=lambda: ("python", "-m", "pytest"))


@dataclass(slots=True)
class PreviewLaunchConfig:
    compose_file: str = "docker-compose.yml"
    service: str = "app"
    host: str = "127.0.0.1"
    host_port: int = 8000


@dataclass(slots=True)
class PreviewLaunchResult:
    command: str
    success: bool
    output: str
    returncode: int
    preview_url: str | None = None


class RuntimeExecutor:
    def run_tests(
        self,
        tests: list[str],
        cwd: Path | str | None = None,
        container: ContainerTestConfig | None = None,
    ) -> CommandResult:
        if not tests:
            return CommandResult(
                command=f"{sys.executable} -m pytest",
                success=False,
                output="No tests scheduled",
                returncode=1,
            )
        working_dir = Path(cwd) if cwd is not None else Path.cwd()
        command = self._build_test_command(tests, container=container)
        env = os.environ.copy()
        if container is None:
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = str(working_dir)
            if existing_pythonpath:
                env["PYTHONPATH"] = f"{working_dir}{os.pathsep}{existing_pythonpath}"
        completed = subprocess.run(
            command,
            cwd=working_dir,
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        output = "\n".join(
            part for part in (completed.stdout.strip(), completed.stderr.strip()) if part
        )
        return CommandResult(
            command=" ".join(command),
            success=completed.returncode == 0,
            output=output,
            returncode=completed.returncode,
        )

    def launch_preview(
        self,
        cwd: Path | str | None = None,
        *,
        config: PreviewLaunchConfig | None = None,
    ) -> PreviewLaunchResult:
        resolved_config = config or PreviewLaunchConfig()
        working_dir = Path(cwd) if cwd is not None else Path.cwd()
        command = [
            "docker",
            "compose",
            "-f",
            resolved_config.compose_file,
            "up",
            "-d",
            "--build",
            resolved_config.service,
        ]
        env = os.environ.copy()
        env["APP_PORT"] = str(resolved_config.host_port)
        try:
            completed = subprocess.run(
                command,
                cwd=working_dir,
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )
        except FileNotFoundError as exc:
            return PreviewLaunchResult(
                command=" ".join(command),
                success=False,
                output=str(exc),
                returncode=127,
                preview_url=None,
            )
        output = "\n".join(
            part for part in (completed.stdout.strip(), completed.stderr.strip()) if part
        )
        preview_url = None
        if completed.returncode == 0:
            preview_url = f"http://{resolved_config.host}:{resolved_config.host_port}/"
        return PreviewLaunchResult(
            command=" ".join(command),
            success=completed.returncode == 0,
            output=output,
            returncode=completed.returncode,
            preview_url=preview_url,
        )

    def _build_test_command(
        self,
        tests: list[str],
        *,
        container: ContainerTestConfig | None = None,
    ) -> list[str]:
        if container is None:
            return [sys.executable, "-m", "pytest", *tests]
        return [
            "docker",
            "compose",
            "-f",
            container.compose_file,
            "run",
            "--rm",
            container.service,
            *container.pytest_command,
            *tests,
        ]
