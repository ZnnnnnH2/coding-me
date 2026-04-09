"""覆盖运行时执行器的单元测试。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from codeingme.runtime import ContainerTestConfig, RuntimeExecutor


def test_runtime_executor_runs_local_tests_with_workspace_pythonpath(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = RuntimeExecutor().run_tests(["tests_generated/test_tasks_demo.py"], cwd=tmp_path)

    assert result.success is True
    assert captured["command"] == [sys.executable, "-m", "pytest", "tests_generated/test_tasks_demo.py"]
    assert captured["cwd"] == tmp_path
    assert str(tmp_path) == captured["env"]["PYTHONPATH"].split(os.pathsep)[0]


def test_runtime_executor_builds_docker_compose_commands_without_pythonpath_injection(
    tmp_path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        env: dict[str, str],
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    config = ContainerTestConfig(compose_file="docker-compose.yml", service="test")

    result = RuntimeExecutor().run_tests(
        ["tests_generated/test_tasks_demo.py"],
        cwd=tmp_path,
        container=config,
    )

    assert result.success is True
    assert captured["command"] == [
        "docker",
        "compose",
        "-f",
        "docker-compose.yml",
        "run",
        "--rm",
        "test",
        "python",
        "-m",
        "pytest",
        "tests_generated/test_tasks_demo.py",
    ]
    assert captured["cwd"] == tmp_path
    assert "PYTHONPATH" not in captured["env"]
