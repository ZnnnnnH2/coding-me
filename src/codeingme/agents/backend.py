"""实现后端代理，用于生成 FastAPI 服务代码。"""

from __future__ import annotations

import ast
import re

from codeingme.agents.base import AgentContext, AgentResult, BaseAgent, StructuredGenerationBundle
from codeingme.agents.naming import generation_plan
from codeingme.runtime import FilePatch, FilePatchPlan


class BackendAgent(BaseAgent):
    role = "backend"

    def run(self, context: AgentContext) -> AgentResult:
        plan = generation_plan(context)
        api_route = self._api_route(context)
        backend_source = self._default_backend_source(context)
        artifacts: dict[str, object] = {
            "service": f"{plan.backend_module_path}::class:{self._service_class_name(context)}",
            "route": self._api_node_id(context),
            "generation_mode": "template",
        }
        bundle, llm_artifacts = self._llm_structured_files(
            context,
            system_prompt=(
                "You are the backend agent in a state-machine-driven app generator. "
                "Return a JSON object with keys summary, files, routes, imports, and risks. "
                "The files field must be a list of generated files."
            ),
            user_prompt=(
                f"Requirement: {context.requirement.summary}\n"
                f"Schemas: {self._schema_summary(context)}\n"
                f"APIs: {self._api_summary(context)}\n"
                "Response format:\n"
                "- Return JSON only.\n"
                f'- Include a files array with one object for path "{plan.backend_module_path}".\n'
                '- Each file object must use keys path, language, and content.\n'
                '- The content value may be a plain Python string or a ```python fenced block.\n'
                f'- Include routes as a list like ["GET {api_route}"].\n'
                '- Include imports as a list of import identifiers.\n'
                '- Include risks as a list of short risk notes.\n'
                "Constraints:\n"
                "- Use FastAPI.\n"
                "- Implement in-memory list-oriented storage behind a service class whose name ends with Service and exposes a list_... method.\n"
                '- Expose a module-level variable named app.\n'
                f'- Implement GET {api_route} returning {{"{plan.response_key}": [...]}}.\n'
                "- Include two in-memory records with id, title, and completed fields.\n"
                "- Let the sample titles, app title, and route copy reflect the requirement domain rather than defaulting to a generic demo.\n"
                "- Keep the module backend-only. Do not add HTML routes, templates, or frontend assets.\n"
                "- Do not include any prose outside the JSON object."
            ),
            max_tokens=1100,
            required_files={plan.backend_module_path: "python"},
            collection_fields=["routes", "imports", "risks"],
            validator=lambda bundle: self._is_valid_backend_bundle(
                bundle,
                backend_module_path=plan.backend_module_path,
                api_route=api_route,
                response_key=plan.response_key,
            ),
        )
        artifacts.update(llm_artifacts)
        if bundle is not None:
            llm_source = self._file_content(bundle, plan.backend_module_path)
            if llm_source is not None:
                backend_source = llm_source
            artifacts["routes"] = bundle.collections["routes"] or self._infer_routes(llm_source or "")
            artifacts["imports"] = bundle.collections["imports"] or self._infer_imports(llm_source or "")
            artifacts["risks"] = bundle.collections["risks"]
            artifacts["generation_mode"] = "llm"

        return AgentResult(
            role=self.role,
            summary="Generated a FastAPI backend module with API contract coverage",
            artifacts=artifacts,
            file_plan=FilePatchPlan(
                name="backend_demo",
                patches=[
                    FilePatch(
                        path=plan.backend_package_init_path,
                        content=f"from .{self._module_name(plan.backend_module_path)} import app\n",
                    ),
                    FilePatch(path=plan.backend_module_path, content=backend_source),
                ],
            ),
        )

    def _default_backend_source(self, context: AgentContext) -> str:
        plan = generation_plan(context)
        service_class_name = self._service_class_name(context)
        service_var_name = self._service_var_name(context)
        api_route = self._api_route(context)
        app_title = self._app_title(context)
        item_titles = self._sample_item_titles(context)
        list_method_name = self._list_method_name(context)
        schema_name = self._primary_schema_name(context)
        return f"""from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="{self._python_string(app_title)}")

class {schema_name}(BaseModel):
    id: int
    title: str
    completed: bool


class {service_class_name}:
    def __init__(self) -> None:
        self._items = [
            {schema_name}(
                id=1,
                title="{self._python_string(item_titles[0])}",
                completed=False,
            ),
            {schema_name}(
                id=2,
                title="{self._python_string(item_titles[1])}",
                completed=True,
            ),
        ]

    def {list_method_name}(self) -> list[{schema_name}]:
        return list(self._items)


{service_var_name} = {service_class_name}()


@app.get("{api_route}")
async def list_items() -> dict[str, list[dict[str, object]]]:
    return {{
        "{plan.response_key}": [
            item.model_dump() for item in {service_var_name}.{list_method_name}()
        ]
    }}
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

    def _is_valid_backend_bundle(
        self,
        bundle: StructuredGenerationBundle,
        *,
        backend_module_path: str,
        api_route: str,
        response_key: str,
    ) -> bool:
        content = self._file_content(bundle, backend_module_path)
        if content is None or not self._is_python_file(backend_module_path, content):
            return False
        try:
            module = ast.parse(content)
        except SyntaxError:
            return False
        if not self._has_fastapi_app(module):
            return False
        if not self._has_service_class(module):
            return False
        route_map = self._route_functions(module)
        api_handler = route_map.get(("get", api_route))
        if api_handler is None:
            return False
        if not self._returns_payload(api_handler, response_key):
            return False
        if ("get", "/") in route_map:
            return False
        routes = bundle.collections.get("routes", [])
        if routes and routes != [f"GET {api_route}"]:
            return False
        imports = [item.lower() for item in bundle.collections.get("imports", [])]
        return not imports or any("fastapi" in item for item in imports)

    def _has_fastapi_app(self, module: ast.Module) -> bool:
        for node in module.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if value is None or not isinstance(value, ast.Call):
                continue
            if not isinstance(value.func, ast.Name) or value.func.id != "FastAPI":
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "app":
                    return True
        return False

    def _has_service_class(self, module: ast.Module) -> bool:
        for node in module.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not node.name.endswith("Service"):
                continue
            if any(
                isinstance(item, ast.FunctionDef) and item.name.startswith("list_")
                for item in node.body
            ):
                return True
        return False

    def _route_functions(self, module: ast.Module) -> dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef]:
        routes: dict[tuple[str, str], ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for node in module.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue
                if not isinstance(decorator.func, ast.Attribute):
                    continue
                if not decorator.args:
                    continue
                if not isinstance(decorator.args[0], ast.Constant) or not isinstance(decorator.args[0].value, str):
                    continue
                method = decorator.func.attr.lower()
                path = decorator.args[0].value
                routes[(method, path)] = node
        return routes

    def _returns_payload(self, node: ast.FunctionDef | ast.AsyncFunctionDef, response_key: str) -> bool:
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Return):
                continue
            value = candidate.value
            if not isinstance(value, ast.Dict):
                continue
            for key in value.keys:
                if isinstance(key, ast.Constant) and key.value == response_key:
                    return True
        return False

    def _infer_routes(self, content: str) -> list[str]:
        try:
            module = ast.parse(content)
        except SyntaxError:
            return []
        routes = [f"{method.upper()} {path}" for method, path in sorted(self._route_functions(module).keys())]
        return routes

    def _infer_imports(self, content: str) -> list[str]:
        try:
            module = ast.parse(content)
        except SyntaxError:
            return []
        imports: list[str] = []
        for node in module.body:
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imports.extend(f"{node.module}.{alias.name}" for alias in node.names)
        return sorted(dict.fromkeys(imports))

    def _primary_schema_name(self, context: AgentContext) -> str:
        if context.schemas:
            return context.schemas[0].name
        return "Task"

    def _api_route(self, context: AgentContext) -> str:
        if context.apis:
            return context.apis[0].route
        return "/api/tasks"

    def _service_class_name(self, context: AgentContext) -> str:
        schema_name = self._primary_schema_name(context)
        if schema_name.endswith("Service"):
            return schema_name
        return f"{schema_name}Service"

    def _service_var_name(self, context: AgentContext) -> str:
        parts = re.findall(r"[A-Z]?[a-z0-9]+", self._service_class_name(context))
        if not parts:
            return "service"
        return "_".join(part.lower() for part in parts)

    def _api_node_id(self, context: AgentContext) -> str:
        return f"api:get:{self._api_route(context)}"

    def _app_title(self, context: AgentContext) -> str:
        return f"{self._humanize_identifier(self._primary_schema_name(context))} API"

    def _sample_item_titles(self, context: AgentContext) -> list[str]:
        label = self._humanize_identifier(self._primary_schema_name(context)).lower()
        return [
            f"Review {label} queue",
            f"Confirm {label} completion state",
        ]

    def _list_method_name(self, context: AgentContext) -> str:
        plan = generation_plan(context)
        return f"list_{plan.response_key}"

    def _module_name(self, path: str) -> str:
        return self._basename(path).removesuffix(".py")

    def _basename(self, path: str) -> str:
        return path.rsplit("/", 1)[-1]

    def _humanize_identifier(self, value: str) -> str:
        parts = re.findall(r"[A-Z]?[a-z0-9]+", value)
        return " ".join(part.capitalize() for part in parts) or value

    def _python_string(self, value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
