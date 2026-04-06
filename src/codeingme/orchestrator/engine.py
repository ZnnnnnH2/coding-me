from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path

from codeingme.agents import ArchitectAgent, BackendAgent, DevOpsAgent, FrontendAgent, QAAgent
from codeingme.agents.base import AgentContext, AgentResult
from codeingme.ast_pipeline import GraphSynchronizer
from codeingme.contracts import AcceptanceTestGenerator, RequirementSpec
from codeingme.graph import (
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphStore,
    GraphSliceBuilder,
    NodeKind,
    SourceLocation,
)
from codeingme.llm import RelayLLMClient
from codeingme.orchestrator.cascade import CascadePlan, CascadePlanner, CascadeTask
from codeingme.orchestrator.state_machine import ExecutionState, StateMachine
from codeingme.runtime import (
    compact_write_plan,
    FilePatch,
    FilePatchOperation,
    FilePatchPlan,
    PatchApplier,
    RollbackManager,
    RuntimeExecutor,
    ContainerTestConfig,
)


@dataclass(slots=True)
class OrchestrationResult:
    requirement: str
    final_state: str
    states: list[str]
    graph_nodes: list[str]
    blast_radius: list[str]
    cascade_order: list[str] = field(default_factory=list)
    cascade_batches: list[list[str]] = field(default_factory=list)
    cascade_tasks: list[CascadeTask] = field(default_factory=list)
    context_slice_nodes: list[str] = field(default_factory=list)
    graph_sync_added: list[str] = field(default_factory=list)
    graph_sync_removed: list[str] = field(default_factory=list)
    artifacts: dict[str, dict[str, object]] = field(default_factory=dict)
    workspace_root: str = ""
    graph_path: str = ""
    red_test_output: str = ""
    verification_output: str = ""


class CodeingmeOrchestrator:
    def __init__(
        self,
        workspace_root: Path | str | None = None,
        graph_path: Path | str | None = None,
        llm_client: RelayLLMClient | None = None,
    ) -> None:
        default_workspace = Path.cwd() / ".codeingme" / "demo_workspace"
        self.workspace_root = Path(workspace_root) if workspace_root is not None else default_workspace
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.graph_path = Path(graph_path) if graph_path is not None else self.workspace_root / "graph.json"
        self.store = GraphStore.load_json(self.graph_path) if self.graph_path.exists() else GraphStore()
        self.slice_builder = GraphSliceBuilder(self.store)
        self.graph_sync = GraphSynchronizer(self.store)
        self.state_machine = StateMachine()
        self.rollback = RollbackManager()
        self.executor = RuntimeExecutor()
        self.patch_applier = PatchApplier(self.workspace_root)
        self.llm_client = llm_client
        if self.llm_client is None and os.getenv("CODEINGME_ENABLE_LLM") == "1":
            self.llm_client = RelayLLMClient.from_env()
        self.architect = ArchitectAgent()
        self.qa = QAAgent()
        self.backend = BackendAgent()
        self.frontend = FrontendAgent()
        self.devops = DevOpsAgent()
        self.test_generator = AcceptanceTestGenerator()

    def run(self, requirement_text: str) -> OrchestrationResult:
        self._reset_workspace()
        requirement = RequirementSpec(
            title=requirement_text,
            summary=requirement_text,
            acceptance_criteria=["Generate contracts", "Drive red-to-green flow", "Propagate impacted changes"],
        )
        requirement_node = GraphNode(
            node_id="requirement:root",
            kind=NodeKind.REQUIREMENT,
            name=requirement.title,
            summary=requirement.summary,
        )
        self.store.upsert_node(requirement_node)
        self.rollback.save("intake", self._checkpoint_state())
        self.state_machine.transition(ExecutionState.CONTRACT_GENERATION)

        empty_slice = self.slice_builder.from_node_ids({"requirement:root"})
        architect_context = AgentContext(requirement=requirement, graph_slice=empty_slice, llm_client=self.llm_client)
        architect_result = self.architect.run(architect_context)
        schemas, apis = self.architect.bootstrap_specs(architect_context)
        changed_node_id = self._schema_node_id(schemas[0])
        self._sync_contract_nodes(schemas, apis)
        self.state_machine.transition(ExecutionState.TEST_RED)

        generated_tests = self.test_generator.generate("tasks")
        qa_context = AgentContext(
            requirement=requirement,
            graph_slice=self.slice_builder.from_node_ids({node.node_id for node in self.store.nodes()}),
            apis=apis,
            schemas=schemas,
            llm_client=self.llm_client,
        )
        qa_result = self.qa.run(qa_context, generated_tests)
        test_targets = self._test_targets(generated_tests)
        self._apply_and_checkpoint("test_red", qa_result.file_plan)
        red_test_result = self.executor.run_tests(
            test_targets,
            cwd=self.workspace_root,
            container=self._test_execution_config(),
        )
        if red_test_result.success:
            raise RuntimeError("Generated acceptance tests were expected to fail before implementation")

        self.state_machine.transition(ExecutionState.IMPLEMENTATION_LOOP)
        backend_result = self.backend.run(qa_context)
        frontend_result = self.frontend.run(qa_context)
        devops_result = self.devops.run(qa_context)
        implementation_plan = self._combine_plans(
            "implementation",
            [backend_result.file_plan, frontend_result.file_plan, devops_result.file_plan],
        )
        implementation_plan = self._compact_plan_for_apply(implementation_plan)
        self._apply_and_checkpoint("implementation", implementation_plan)
        self.state_machine.transition(ExecutionState.GRAPH_SYNC)

        graph_sync_results = self._sync_generated_python_files([qa_result.file_plan, implementation_plan])
        self._sync_runtime_nodes(schemas, apis, backend_result, frontend_result)
        self.state_machine.transition(ExecutionState.CASCADE_UPDATE)

        planner = CascadePlanner(self.store)
        blast_radius = planner.blast_radius(changed_node_id)
        cascade_plan = planner.execution_plan(changed_node_id)
        backend_result, frontend_result, cascade_sync_results = self._execute_cascade_plan(
            cascade_plan,
            requirement=requirement,
            schemas=schemas,
            apis=apis,
            generated_tests=generated_tests,
            backend_result=backend_result,
            frontend_result=frontend_result,
        )
        cascade_order = cascade_plan.ordered_node_ids
        context_slice = planner.context_slice(changed_node_id, max_hops=1)
        self.state_machine.transition(ExecutionState.VERIFICATION)
        verification_result = self.executor.run_tests(
            test_targets,
            cwd=self.workspace_root,
            container=self._test_execution_config(),
        )
        if not verification_result.success:
            self.state_machine.transition(ExecutionState.ROLLBACK)
            restored = self.rollback.restore(self.workspace_root)
            if restored is not None and "graph" in restored.state_snapshot:
                self.store = GraphStore.from_dict(restored.state_snapshot["graph"])
                self.slice_builder = GraphSliceBuilder(self.store)
                self.graph_sync = GraphSynchronizer(self.store)
            self.store.save_json(self.graph_path)
            raise RuntimeError(
                f"Verification failed; latest checkpoint={restored.name if restored else 'none'}\n"
                f"{verification_result.output}"
            )
        self.state_machine.transition(ExecutionState.DONE)
        self.store.save_json(self.graph_path)

        return OrchestrationResult(
            requirement=requirement_text,
            final_state=self.state_machine.state.value,
            states=[state.value for state in self.state_machine.history] + [self.state_machine.state.value],
            graph_nodes=sorted(node.node_id for node in self.store.nodes()),
            blast_radius=blast_radius,
            cascade_order=cascade_order,
            cascade_batches=cascade_plan.batches,
            cascade_tasks=cascade_plan.tasks,
            context_slice_nodes=sorted(context_slice.node_ids()),
            graph_sync_added=sorted(
                {
                    node_id
                    for result in [*graph_sync_results, *cascade_sync_results]
                    for node_id in result.delta.added_nodes
                }
            ),
            graph_sync_removed=sorted(
                {
                    node_id
                    for result in [*graph_sync_results, *cascade_sync_results]
                    for node_id in result.delta.removed_nodes
                }
            ),
            artifacts={
                architect_result.role: architect_result.artifacts,
                qa_result.role: {test.name: test.expected_state for test in qa_result.tests},
                backend_result.role: backend_result.artifacts,
                frontend_result.role: frontend_result.artifacts,
                devops_result.role: devops_result.artifacts,
            },
            workspace_root=str(self.workspace_root),
            graph_path=str(self.graph_path),
            red_test_output=red_test_result.output,
            verification_output=verification_result.output,
        )

    def _checkpoint_state(self) -> dict[str, object]:
        return {
            "state": self.state_machine.state.value,
            "graph": self.store.to_dict(),
        }

    def _apply_and_checkpoint(self, name: str, plan: FilePatchPlan | None) -> None:
        if plan is None or not plan.patches:
            self.rollback.save(name, self._checkpoint_state())
            return
        applied = self.patch_applier.apply(plan)
        self.rollback.save(name, self._checkpoint_state(), applied_patches=applied)

    def _combine_plans(self, name: str, plans: list[FilePatchPlan | None]) -> FilePatchPlan:
        patches: list[FilePatch] = []
        for plan in plans:
            if plan is not None:
                patches.extend(plan.patches)
        return FilePatchPlan(name=name, patches=patches)

    def _compact_plan_for_apply(self, plan: FilePatchPlan | None) -> FilePatchPlan | None:
        if plan is None or not plan.patches:
            return plan
        return compact_write_plan(self.workspace_root, plan)

    def _test_execution_config(self) -> ContainerTestConfig | None:
        if os.getenv("CODEINGME_RUN_TESTS_IN_DOCKER") != "1":
            return None
        compose_file = self.workspace_root / "docker-compose.yml"
        if not compose_file.exists():
            return None
        return ContainerTestConfig(compose_file="docker-compose.yml", service="test")

    def _reset_workspace(self) -> None:
        cleanup_plan = FilePatchPlan(
            name="cleanup",
            patches=[
                FilePatch(path=".dockerignore", operation=FilePatchOperation.DELETE),
                FilePatch(path="Dockerfile", operation=FilePatchOperation.DELETE),
                FilePatch(path="demo_app/__init__.py", operation=FilePatchOperation.DELETE),
                FilePatch(path="demo_app/tasks_api.py", operation=FilePatchOperation.DELETE),
                FilePatch(path="demo_app/static/task_list.html", operation=FilePatchOperation.DELETE),
                FilePatch(path="docker-compose.yml", operation=FilePatchOperation.DELETE),
                FilePatch(path="tests_generated/test_tasks_demo.py", operation=FilePatchOperation.DELETE),
            ],
        )
        self.patch_applier.apply(cleanup_plan)

    def _sync_generated_python_files(self, plans: list[FilePatchPlan | None]) -> list[object]:
        results: list[object] = []
        seen_paths: set[str] = set()
        for plan in plans:
            if plan is None:
                continue
            for patch in plan.patches:
                if patch.path in seen_paths or not patch.path.endswith(".py"):
                    continue
                seen_paths.add(patch.path)
                file_path = self.workspace_root / patch.path
                source = file_path.read_text(encoding="utf-8") if file_path.exists() else ""
                results.append(self.graph_sync.sync_source(source, file_path=patch.path))
        return results

    def _sync_contract_nodes(self, schemas: list[object], apis: list[object]) -> None:
        for schema in schemas:
            schema_node = GraphNode(
                node_id=self._schema_node_id(schema),
                kind=NodeKind.DATA_MODEL,
                name=schema.name,
                summary=", ".join(f"{key}:{value}" for key, value in schema.fields.items()),
            )
            self.store.upsert_node(schema_node)
            self.store.add_edge(GraphEdge("requirement:root", schema_node.node_id, GraphEdgeType.GENERATES))
        for api in apis:
            api_node = GraphNode(
                node_id=self._api_node_id(api),
                kind=NodeKind.API_ROUTE,
                name=f"{api.method} {api.route}",
                summary=api.summary,
            )
            self.store.upsert_node(api_node)
            self.store.add_edge(GraphEdge("requirement:root", api_node.node_id, GraphEdgeType.GENERATES))

    def _sync_runtime_nodes(
        self,
        schemas: list[object],
        apis: list[object],
        backend_result: object,
        frontend_result: object,
    ) -> None:
        backend_content = self._content_for_path("demo_app/tasks_api.py", [backend_result.file_plan])
        frontend_content = self._content_for_path("demo_app/static/task_list.html", [frontend_result.file_plan])

        schema = schemas[0]
        api = apis[0]
        schema_node_id = self._schema_node_id(schema)
        api_node_id = self._api_node_id(api)
        self.store.upsert_node(
            GraphNode(
                node_id=schema_node_id,
                kind=NodeKind.DATA_MODEL,
                name=schema.name,
                summary=", ".join(f"{key}:{value}" for key, value in schema.fields.items()),
                source=self._source_location("demo_app/tasks_api.py", backend_content, "_tasks = ["),
            )
        )
        self.store.upsert_node(
            GraphNode(
                node_id=api_node_id,
                kind=NodeKind.API_ROUTE,
                name=f"{api.method} {api.route}",
                summary=api.summary,
                source=self._source_location("demo_app/tasks_api.py", backend_content, api.route),
            )
        )
        self.store.upsert_node(
            GraphNode(
                "ui:task_list",
                NodeKind.UI_COMPONENT,
                "TaskList",
                frontend_result.summary,
                source=self._source_location("demo_app/static/task_list.html", frontend_content, '<ul id="task-list">'),
            )
        )
        self.store.add_edge(GraphEdge(api_node_id, schema_node_id, GraphEdgeType.DEPENDS_ON))
        self.store.add_edge(GraphEdge("ui:task_list", api_node_id, GraphEdgeType.CALLS_API))

    def _execute_cascade_plan(
        self,
        cascade_plan: CascadePlan,
        *,
        requirement: RequirementSpec,
        schemas: list[object],
        apis: list[object],
        generated_tests: list[object],
        backend_result: AgentResult,
        frontend_result: AgentResult,
    ) -> tuple[AgentResult, AgentResult, list[object]]:
        latest_backend_result = backend_result
        latest_frontend_result = frontend_result
        graph_sync_results: list[object] = []
        tasks_by_node_id = {task.node_id: task for task in cascade_plan.tasks}

        for batch_index, batch in enumerate(cascade_plan.batches, start=1):
            batch_tasks = [
                tasks_by_node_id[node_id]
                for node_id in batch
                if node_id != cascade_plan.changed_node_id
            ]
            if not batch_tasks:
                continue

            role_context_node_ids: dict[str, set[str]] = {}
            role_order: list[str] = []
            for task in batch_tasks:
                if task.role not in {"backend", "frontend", "qa"}:
                    continue
                role_context_node_ids.setdefault(task.role, set()).update(task.context_node_ids)
                if task.role not in role_order:
                    role_order.append(task.role)

            role_results: dict[str, AgentResult] = {}
            role_plans: list[FilePatchPlan | None] = []
            for role in role_order:
                context = AgentContext(
                    requirement=requirement,
                    graph_slice=self.slice_builder.from_node_ids(role_context_node_ids[role]),
                    apis=apis,
                    schemas=schemas,
                    llm_client=self.llm_client,
                )
                result = self._run_cascade_role(role, context, generated_tests)
                role_results[role] = result
                role_plans.append(result.file_plan)

            if "backend" in role_results:
                latest_backend_result = role_results["backend"]
            if "frontend" in role_results:
                latest_frontend_result = role_results["frontend"]

            batch_plan = self._combine_plans(f"cascade_batch_{batch_index}", role_plans)
            batch_plan = self._compact_plan_for_apply(batch_plan)
            if batch_plan is None or not batch_plan.patches:
                continue

            self._apply_and_checkpoint(batch_plan.name, batch_plan)
            graph_sync_results.extend(self._sync_generated_python_files([batch_plan]))
            self._sync_runtime_nodes(schemas, apis, latest_backend_result, latest_frontend_result)

        return latest_backend_result, latest_frontend_result, graph_sync_results

    def _run_cascade_role(
        self,
        role: str,
        context: AgentContext,
        generated_tests: list[object],
    ) -> AgentResult:
        if role == "backend":
            return self.backend.run(context)
        if role == "frontend":
            return self.frontend.run(context)
        if role == "qa":
            return self.qa.run(context, generated_tests)
        raise ValueError(f"Unsupported cascade role: {role}")

    def _content_for_path(self, path: str, plans: list[FilePatchPlan | None]) -> str:
        for plan in plans:
            if plan is None:
                continue
            for patch in plan.patches:
                if patch.path == path and patch.content is not None:
                    return patch.content
        return ""

    def _source_location(self, file_path: str, content: str, marker: str) -> SourceLocation:
        lines = content.splitlines()
        start_line = next((index for index, line in enumerate(lines, start=1) if marker in line), 1)
        end_line = len(lines) if lines else 1
        return SourceLocation(file_path=file_path, start_line=start_line, end_line=end_line)

    def _test_targets(self, tests: list[object]) -> list[str]:
        targets = {test.path for test in tests if getattr(test, "path", None)}
        return sorted(target for target in targets if target is not None)

    def _schema_node_id(self, schema: object) -> str:
        return f"schema:{getattr(schema, 'name').lower()}"

    def _api_node_id(self, api: object) -> str:
        return f"api:{getattr(api, 'method').lower()}:{getattr(api, 'route')}"
