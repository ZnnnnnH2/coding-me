from __future__ import annotations

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent, StructuredGenerationBundle
from codeingme.agents.naming import generation_plan
from codeingme.runtime import FilePatch, FilePatchPlan


class DevOpsAgent(BaseAgent):
    role = "devops"

    def run(self, context: AgentContext) -> AgentResult:
        module_ref = f"demo_app.{self._backend_module_name(context)}:app"
        dockerfile = self._default_dockerfile(context)
        compose_file = self._default_compose_file(context)
        dockerignore = self._default_dockerignore()
        artifacts: dict[str, object] = {
            "docker": "docker compose up app",
            "ci": "docker compose run --rm test python -m pytest tests_generated",
            "compose_service": "test",
            "generation_mode": "template",
        }
        bundle, llm_artifacts = self._llm_structured_files(
            context,
            system_prompt=(
                "You are the DevOps agent in a state-machine-driven app generator. "
                "Return a JSON object with keys summary, files, services, commands, and risks. "
                "The files field must be a list of generated files."
            ),
            user_prompt=(
                f"Requirement: {context.requirement.summary}\n"
                f"APIs: {self._api_summary(context)}\n"
                f"Schemas: {self._schema_summary(context)}\n"
                "Response format:\n"
                "- Return JSON only.\n"
                '- Include files for "Dockerfile", "docker-compose.yml", and ".dockerignore".\n'
                "- Do not include application source files, tests, or placeholder package files.\n"
                '- Each file object must use keys path, language, and content.\n'
                '- The content value may be plain text or a fenced code block.\n'
                '- Include services as a list like ["app", "test"].\n'
                '- Include commands exactly as ["docker compose up app", "docker compose run --rm test python -m pytest tests_generated"].\n'
                '- Include risks as a list of short risk notes.\n'
                "Constraints:\n"
                "- Build from python:3.11-slim.\n"
                "- Copy demo_app and tests_generated into /workspace.\n"
                "- Install fastapi, httpx, pytest, and uvicorn.\n"
                f"- The app service must run {module_ref} with uvicorn on port 8000.\n"
                "- The test service must run python -m pytest tests_generated.\n"
                "- Configure PYTHONPATH=/workspace in the container.\n"
                "- Do not include any prose outside the JSON object."
            ),
            max_tokens=1200,
            required_files={
                "Dockerfile": "dockerfile",
                "docker-compose.yml": "yaml",
                ".dockerignore": "text",
            },
            collection_fields=["services", "commands", "risks"],
            validator=self._is_valid_devops_bundle,
        )
        artifacts.update(llm_artifacts)
        if bundle is not None:
            llm_dockerfile = self._file_content(bundle, "Dockerfile")
            llm_compose = self._file_content(bundle, "docker-compose.yml")
            llm_dockerignore = self._file_content(bundle, ".dockerignore")
            if llm_dockerfile is not None:
                dockerfile = llm_dockerfile
            if llm_compose is not None:
                compose_file = llm_compose
            if llm_dockerignore is not None:
                dockerignore = llm_dockerignore
            artifacts["services"] = bundle.collections["services"]
            artifacts["commands"] = bundle.collections["commands"]
            artifacts["risks"] = bundle.collections["risks"]
            artifacts["generation_mode"] = "llm"

        return AgentResult(
            role=self.role,
            summary="Prepared containerized runtime and verification artifacts",
            artifacts=artifacts,
            file_plan=FilePatchPlan(
                name="devops_demo",
                patches=[
                    FilePatch(path="Dockerfile", content=dockerfile),
                    FilePatch(path="docker-compose.yml", content=compose_file),
                    FilePatch(path=".dockerignore", content=dockerignore),
                ],
            ),
        )

    def _default_dockerfile(self, context: AgentContext) -> str:
        module_ref = f"demo_app.{self._backend_module_name(context)}:app"
        return f"""FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir fastapi httpx pytest uvicorn

COPY demo_app ./demo_app
COPY tests_generated ./tests_generated

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "{module_ref}", "--host", "0.0.0.0", "--port", "8000"]
"""

    def _default_compose_file(self, context: AgentContext) -> str:
        module_ref = f"demo_app.{self._backend_module_name(context)}:app"
        return f"""services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    working_dir: /workspace
    environment:
      PYTHONPATH: /workspace
    command: python -m uvicorn {module_ref} --host 0.0.0.0 --port 8000
    ports:
      - "${{APP_PORT:-8000}}:8000"
  test:
    build:
      context: .
      dockerfile: Dockerfile
    working_dir: /workspace
    environment:
      PYTHONPATH: /workspace
    command: python -m pytest tests_generated
"""

    def _default_dockerignore(self) -> str:
        return """__pycache__/
.pytest_cache/
.venv/
graph.json
"""

    def _schema_summary(self, context: AgentContext) -> str:
        if not context.schemas:
            return "none"
        return "; ".join(
            f"{schema.name}({', '.join(f'{key}:{value}' for key, value in schema.fields.items())})"
            for schema in context.schemas
        )

    def _api_summary(self, context: AgentContext) -> str:
        if not context.apis:
            return "none"
        return "; ".join(f"{api.method} {api.route}" for api in context.apis)

    def _backend_module_name(self, context: AgentContext) -> str:
        plan = generation_plan(context)
        return plan.backend_module_path.rsplit("/", 1)[-1].removesuffix(".py")

    def _is_valid_devops_bundle(self, bundle: StructuredGenerationBundle) -> bool:
        dockerfile = self._file_content(bundle, "Dockerfile")
        compose_file = self._file_content(bundle, "docker-compose.yml")
        dockerignore = self._file_content(bundle, ".dockerignore")
        if not dockerfile or not compose_file or not dockerignore:
            return False
        required_dockerfile_fragments = [
            "FROM python:3.11-slim",
            "WORKDIR /workspace",
            "fastapi",
            "pytest",
            "uvicorn",
            "COPY demo_app ./demo_app",
            "COPY tests_generated ./tests_generated",
            "demo_app.",
            ":app",
        ]
        required_compose_fragments = [
            "services:",
            "app:",
            "test:",
            "dockerfile: Dockerfile",
            "PYTHONPATH: /workspace",
            "python -m pytest tests_generated",
        ]
        required_dockerignore_fragments = [
            "__pycache__/",
            ".pytest_cache/",
        ]
        if not all(fragment in dockerfile for fragment in required_dockerfile_fragments):
            return False
        if not all(fragment in compose_file for fragment in required_compose_fragments):
            return False
        if not all(fragment in dockerignore for fragment in required_dockerignore_fragments):
            return False
        if not self._has_items(bundle.collections.get("services", []), ["app", "test"]):
            return False
        if not self._contains_uvicorn_command(dockerfile):
            return False
        if not self._contains_uvicorn_command(compose_file):
            return False
        commands = bundle.collections.get("commands", [])
        return (
            "docker compose up app" in commands
            and "docker compose run --rm test python -m pytest tests_generated" in commands
        )

    def _contains_uvicorn_command(self, content: str) -> bool:
        return (
            "uvicorn" in content
            and "demo_app." in content
            and ":app" in content
            and "--host" in content
            and "0.0.0.0" in content
            and "--port" in content
            and "8000" in content
        )
