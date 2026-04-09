"""实现主编排器，串联生成、修复与验证流程。"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
from typing import Callable

from codeingme.agents import ArchitectAgent, BackendAgent, DevOpsAgent, QAAgent
from codeingme.agents.base import AgentContext, AgentResult
from codeingme.agents.naming import generation_plan
from codeingme.ast_pipeline import GraphSynchronizer
from codeingme.contracts import APISpec, AcceptanceTestGenerator, DataSchema, RequirementSpec, TestSpec
from codeingme.graph import (
    GraphEdge,
    GraphEdgeType,
    GraphNode,
    GraphStore,
    GraphSliceBuilder,
    NodeKind,
    SourceLocation,
)
from codeingme.llm import LLMConfig, RelayLLMClient
from codeingme.orchestrator.cascade import CascadePlan, CascadePlanner, CascadeTask
from codeingme.orchestrator.state_machine import ExecutionState, StateMachine
from codeingme.runtime import (
    compact_write_plan,
    FilePatch,
    FilePatchOperation,
    FilePatchPlan,
    PatchApplier,
    render_patch_unified_diff,
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


@dataclass(slots=True)
class OrchestrationEvent:
    stage: str
    status: str
    message: str
    state: str | None = None
    role: str | None = None
    batch: int | None = None
    details: dict[str, object] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


EventCallback = Callable[[OrchestrationEvent], None]


class CodeingmeOrchestrator:
    def __init__(
        self,
        workspace_root: Path | str | None = None,
        graph_path: Path | str | None = None,
        llm_client: RelayLLMClient | None = None,
    ) -> None:
        default_workspace = Path.cwd() / ".codeingme" / "runs" / "direct" / "adhoc" / "current" / "workspace"
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
            if self.llm_client is None:
                raise RuntimeError(LLMConfig.missing_required_env_message())
        self.architect = ArchitectAgent()
        self.qa = QAAgent()
        self.backend = BackendAgent()
        self.devops = DevOpsAgent()
        self.test_generator = AcceptanceTestGenerator()

    def run(
        self,
        requirement_text: str,
        event_callback: EventCallback | None = None,
    ) -> OrchestrationResult:
        self._ensure_llm_client()
        self.store = GraphStore()
        self.slice_builder = GraphSliceBuilder(self.store)
        self.graph_sync = GraphSynchronizer(self.store)
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
        self._emit(
            event_callback,
            stage="run",
            status="started",
            state=self.state_machine.state.value,
            message="Accepted the structured requirement and reset the demo workspace.",
            details={"requirement": requirement_text},
        )
        self._transition(
            ExecutionState.CONTRACT_GENERATION,
            event_callback,
            "Architect agent is drafting contracts from the uploaded specification bundle.",
        )

        empty_slice = self.slice_builder.from_node_ids({"requirement:root"})
        architect_context = AgentContext(requirement=requirement, graph_slice=empty_slice, llm_client=self.llm_client)
        architect_result = self._run_agent(
            "architect",
            architect_context,
            lambda: self.architect.run(architect_context),
            event_callback,
            start_message="Generating initial schemas and API contracts.",
        )
        schemas, apis = self.architect.bootstrap_specs(architect_context)
        plan = generation_plan(
            AgentContext(
                requirement=requirement,
                graph_slice=empty_slice,
                apis=apis,
                schemas=schemas,
                llm_client=self.llm_client,
            )
        )
        changed_node_id = self._schema_node_id(schemas[0])
        self._sync_contract_nodes(schemas, apis)
        self._emit(
            event_callback,
            stage="contracts",
            status="completed",
            state=self.state_machine.state.value,
            message="Contract nodes were synced into the graph store.",
            details={
                "schemas": [getattr(schema, "name", "") for schema in schemas],
                "apis": [f"{getattr(api, 'method', '')} {getattr(api, 'route', '')}" for api in apis],
                "schemas_data": [
                    {
                        "name": getattr(schema, "name", ""),
                        "fields": dict(getattr(schema, "fields", {})),
                    }
                    for schema in schemas
                ],
                "apis_data": [
                    {
                        "route": getattr(api, "route", ""),
                        "method": getattr(api, "method", ""),
                        "summary": getattr(api, "summary", ""),
                        "request_schema": getattr(api, "request_schema", None),
                        "response_schema": getattr(api, "response_schema", None),
                    }
                    for api in apis
                ],
            },
        )
        self._transition(
            ExecutionState.TEST_RED,
            event_callback,
            "QA agent is drafting failing acceptance tests before implementation begins.",
        )

        generated_tests = self.test_generator.generate(plan.plural_slug)
        qa_context = AgentContext(
            requirement=requirement,
            graph_slice=self.slice_builder.from_node_ids({node.node_id for node in self.store.nodes()}),
            apis=apis,
            schemas=schemas,
            llm_client=self.llm_client,
        )
        qa_result = self._run_agent(
            "qa",
            qa_context,
            lambda: self.qa.run(qa_context, generated_tests),
            event_callback,
            start_message="Generating red tests and harnesses for the target backend module.",
        )
        test_targets = self._test_targets(generated_tests)
        self._apply_and_checkpoint("test_red", qa_result.file_plan)
        self._emit(
            event_callback,
            stage="patch",
            status="completed",
            state=self.state_machine.state.value,
            role="qa",
            message="Applied generated red-test patches to the run workspace.",
            details={"patch_count": self._patch_count(qa_result.file_plan)},
        )
        self._emit(
            event_callback,
            stage="tests",
            status="started",
            state=self.state_machine.state.value,
            message="Running generated acceptance tests to confirm the workspace is red before implementation.",
            details={"targets": test_targets},
        )
        red_test_result = self.executor.run_tests(
            test_targets,
            cwd=self.workspace_root,
            container=self._test_execution_config(),
        )
        self._emit(
            event_callback,
            stage="tests",
            status="expected_failure" if not red_test_result.success else "unexpected_pass",
            state=self.state_machine.state.value,
            message=(
                "Red test phase behaved as expected and the generated tests failed before implementation."
                if not red_test_result.success
                else "Generated acceptance tests passed unexpectedly before implementation."
            ),
            details={
                "command": red_test_result.command,
                "returncode": red_test_result.returncode,
            },
        )
        if red_test_result.success:
            raise RuntimeError("Generated acceptance tests were expected to fail before implementation")

        self._transition(
            ExecutionState.IMPLEMENTATION_LOOP,
            event_callback,
            "Implementation agents are now generating backend and devops assets.",
        )
        backend_result = self._run_agent(
            "backend",
            qa_context,
            lambda: self.backend.run(qa_context),
            event_callback,
            start_message="Backend agent is generating the FastAPI service and API contract surface.",
        )
        devops_result = self._run_agent(
            "devops",
            qa_context,
            lambda: self.devops.run(qa_context),
            event_callback,
            start_message="DevOps agent is preparing container and runtime wiring for the generated module.",
        )
        implementation_plan = self._combine_plans(
            "implementation",
            [backend_result.file_plan, devops_result.file_plan],
        )
        implementation_plan = self._compact_plan_for_apply(implementation_plan)
        self._apply_and_checkpoint("implementation", implementation_plan)
        self._emit(
            event_callback,
            stage="patch",
            status="completed",
            state=self.state_machine.state.value,
            message="Applied implementation patches from backend and devops agents.",
            details={"patch_count": self._patch_count(implementation_plan)},
        )
        self._transition(
            ExecutionState.GRAPH_SYNC,
            event_callback,
            "Synchronizing generated Python files back into the graph model.",
        )

        graph_sync_results = self._sync_generated_python_files([qa_result.file_plan, implementation_plan])
        self._sync_runtime_nodes(qa_context, backend_result)
        self._emit(
            event_callback,
            stage="graph_sync",
            status="completed",
            state=self.state_machine.state.value,
            message="Graph synchronization finished for the latest generated runtime files.",
            details={
                "synced_files": self._python_patch_count([qa_result.file_plan, implementation_plan]),
                "added_nodes": sum(len(result.delta.added_nodes) for result in graph_sync_results),
                "removed_nodes": sum(len(result.delta.removed_nodes) for result in graph_sync_results),
            },
        )
        self._transition(
            ExecutionState.CASCADE_UPDATE,
            event_callback,
            "Planner is executing graph-aware cascade updates for impacted backend and QA nodes.",
        )

        planner = CascadePlanner(self.store)
        blast_radius = planner.blast_radius(changed_node_id)
        cascade_plan = planner.execution_plan(changed_node_id)
        backend_result, cascade_sync_results = self._execute_cascade_plan(
            cascade_plan,
            requirement=requirement,
            schemas=schemas,
            apis=apis,
            generated_tests=generated_tests,
            backend_result=backend_result,
            event_callback=event_callback,
        )
        cascade_order = cascade_plan.ordered_node_ids
        context_slice = planner.context_slice(changed_node_id, max_hops=1)
        self._emit(
            event_callback,
            stage="cascade",
            status="completed",
            state=self.state_machine.state.value,
            message="Cascade update batches finished and the focused graph slice is ready for final verification.",
            details={
                "blast_radius": blast_radius,
                "batches": cascade_plan.batches,
            },
        )
        self._transition(
            ExecutionState.VERIFICATION,
            event_callback,
            "Running final verification tests against the generated backend module.",
        )
        self._emit(
            event_callback,
            stage="tests",
            status="started",
            state=self.state_machine.state.value,
            message="Executing verification suite for the generated backend module.",
            details={"targets": test_targets},
        )
        verification_result = self.executor.run_tests(
            test_targets,
            cwd=self.workspace_root,
            container=self._test_execution_config(),
        )
        if not verification_result.success:
            self._emit(
                event_callback,
                stage="tests",
                status="failed",
                state=self.state_machine.state.value,
                message="Verification failed. Rolling the workspace back to the latest safe checkpoint.",
                details={
                    "command": verification_result.command,
                    "returncode": verification_result.returncode,
                },
            )
            self._transition(
                ExecutionState.ROLLBACK,
                event_callback,
                "Rollback manager is restoring the workspace after a failed verification pass.",
            )
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
        self._emit(
            event_callback,
            stage="tests",
            status="completed",
            state=self.state_machine.state.value,
            message="Verification suite passed and the generated backend module is accepted.",
            details={
                "command": verification_result.command,
                "returncode": verification_result.returncode,
            },
        )
        self._transition(
            ExecutionState.DONE,
            event_callback,
            "Run completed successfully. Generated backend artifacts are ready to inspect.",
        )
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
                qa_result.role: {
                    **qa_result.artifacts,
                    "test_expectations": {
                        test.name: test.expected_state for test in qa_result.tests
                    },
                },
                backend_result.role: backend_result.artifacts,
                devops_result.role: devops_result.artifacts,
            },
            workspace_root=str(self.workspace_root),
            graph_path=str(self.graph_path),
            red_test_output=red_test_result.output,
            verification_output=verification_result.output,
        )

    def resume(
        self,
        requirement_text: str,
        *,
        resume_from: ExecutionState | str,
        schemas_data: list[dict[str, object]] | None = None,
        apis_data: list[dict[str, object]] | None = None,
        generated_tests_data: list[dict[str, object]] | None = None,
        prior_artifacts: dict[str, dict[str, object]] | None = None,
        red_test_output: str = "",
        event_callback: EventCallback | None = None,
    ) -> OrchestrationResult:
        self._ensure_llm_client()
        resume_state = resume_from if isinstance(resume_from, ExecutionState) else ExecutionState(resume_from)
        self.store = GraphStore.load_json(self.graph_path) if self.graph_path.exists() else GraphStore()
        self.slice_builder = GraphSliceBuilder(self.store)
        self.graph_sync = GraphSynchronizer(self.store)
        self.rollback = RollbackManager()

        requirement = self._requirement_spec(requirement_text)
        requirement_node = GraphNode(
            node_id="requirement:root",
            kind=NodeKind.REQUIREMENT,
            name=requirement.title,
            summary=requirement.summary,
        )
        self.store.upsert_node(requirement_node)

        prior_artifacts = copy.deepcopy(prior_artifacts or {})
        schemas = [DataSchema(name=item["name"], fields=dict(item["fields"])) for item in (schemas_data or [])]
        apis = [
            APISpec(
                route=item["route"],
                method=item["method"],
                summary=item.get("summary", "") or f"{item['method']} {item['route']}",
                request_schema=item.get("request_schema"),
                response_schema=item.get("response_schema"),
            )
            for item in (apis_data or [])
        ]
        generated_tests = [
            TestSpec(
                name=item["name"],
                description=item.get("description", ""),
                expected_state=item.get("expected_state", "green"),
                path=item.get("path"),
            )
            for item in (generated_tests_data or [])
        ]

        self._emit(
            event_callback,
            stage="run",
            status="started",
            state="intake",
            message=f"Resuming the run from {resume_state.value}.",
            details={"requirement": requirement_text, "resume_from": resume_state.value},
        )

        if resume_state is ExecutionState.CONTRACT_GENERATION:
            self.state_machine = StateMachine()
            return self.run(requirement_text, event_callback=event_callback)

        if not schemas or not apis:
            raise RuntimeError("Resume requires persisted contract data from a completed contract_generation stage.")

        self._sync_contract_nodes(schemas, apis)
        plan = generation_plan(
            AgentContext(
                requirement=requirement,
                graph_slice=self.slice_builder.from_node_ids({"requirement:root"}),
                apis=apis,
                schemas=schemas,
                llm_client=self.llm_client,
            )
        )
        if not generated_tests:
            generated_tests = self.test_generator.generate(plan.plural_slug)

        architect_artifacts = prior_artifacts.get("architect") or self._architect_artifacts_from_contracts(schemas, apis)
        qa_artifacts = prior_artifacts.get("qa") or self._qa_artifacts_from_tests(generated_tests)
        architect_result = AgentResult(role="architect", summary="Resumed from persisted contract state", artifacts=architect_artifacts)
        qa_result = AgentResult(role="qa", summary="Resumed from persisted test state", artifacts=qa_artifacts, tests=generated_tests)

        changed_node_id = self._schema_node_id(schemas[0])
        test_targets = self._test_targets(generated_tests)
        qa_context = AgentContext(
            requirement=requirement,
            graph_slice=self.slice_builder.from_node_ids({node.node_id for node in self.store.nodes()}),
            apis=apis,
            schemas=schemas,
            llm_client=self.llm_client,
        )

        if resume_state is ExecutionState.TEST_RED:
            self._prime_state_machine(ExecutionState.CONTRACT_GENERATION)
            self._transition(
                ExecutionState.TEST_RED,
                event_callback,
                "Resuming from test_red and regenerating red tests before implementation.",
            )
            qa_result = self._run_agent(
                "qa",
                qa_context,
                lambda: self.qa.run(qa_context, generated_tests),
                event_callback,
                start_message="Regenerating red tests and harnesses for the target backend module.",
            )
            test_targets = self._test_targets(generated_tests)
            self._apply_and_checkpoint("test_red", qa_result.file_plan)
            self._emit(
                event_callback,
                stage="patch",
                status="completed",
                state=self.state_machine.state.value,
                role="qa",
                message="Applied regenerated red-test patches to the run workspace.",
                details={"patch_count": self._patch_count(qa_result.file_plan)},
            )
            self._emit(
                event_callback,
                stage="tests",
                status="started",
                state=self.state_machine.state.value,
                message="Running generated acceptance tests to confirm the workspace is red before implementation.",
                details={"targets": test_targets},
            )
            red_test_result = self.executor.run_tests(
                test_targets,
                cwd=self.workspace_root,
                container=self._test_execution_config(),
            )
            self._emit(
                event_callback,
                stage="tests",
                status="expected_failure" if not red_test_result.success else "unexpected_pass",
                state=self.state_machine.state.value,
                message=(
                    "Red test phase behaved as expected and the generated tests failed before implementation."
                    if not red_test_result.success
                    else "Generated acceptance tests passed unexpectedly before implementation."
                ),
                details={
                    "command": red_test_result.command,
                    "returncode": red_test_result.returncode,
                },
            )
            if red_test_result.success:
                raise RuntimeError("Generated acceptance tests were expected to fail before implementation")
            red_test_output = red_test_result.output
            prior_artifacts["qa"] = qa_result.artifacts
        else:
            self._prime_state_machine(ExecutionState.TEST_RED)

        self._transition(
            ExecutionState.IMPLEMENTATION_LOOP,
            event_callback,
            "Implementation agents are now generating backend and devops assets.",
        )
        backend_result = self._run_agent(
            "backend",
            qa_context,
            lambda: self.backend.run(qa_context),
            event_callback,
            start_message="Backend agent is generating the FastAPI service and API contract surface.",
        )
        devops_result = self._run_agent(
            "devops",
            qa_context,
            lambda: self.devops.run(qa_context),
            event_callback,
            start_message="DevOps agent is preparing container and runtime wiring for the generated module.",
        )
        implementation_plan = self._combine_plans(
            "implementation",
            [backend_result.file_plan, devops_result.file_plan],
        )
        implementation_plan = self._compact_plan_for_apply(implementation_plan)
        self._apply_and_checkpoint("implementation", implementation_plan)
        self._emit(
            event_callback,
            stage="patch",
            status="completed",
            state=self.state_machine.state.value,
            message="Applied implementation patches from backend and devops agents.",
            details={"patch_count": self._patch_count(implementation_plan)},
        )
        self._transition(
            ExecutionState.GRAPH_SYNC,
            event_callback,
            "Synchronizing generated Python files back into the graph model.",
        )

        graph_sync_results = self._sync_generated_python_files([qa_result.file_plan, implementation_plan])
        self._sync_runtime_nodes(qa_context, backend_result)
        self._emit(
            event_callback,
            stage="graph_sync",
            status="completed",
            state=self.state_machine.state.value,
            message="Graph synchronization finished for the latest generated runtime files.",
            details={
                "synced_files": self._python_patch_count([qa_result.file_plan, implementation_plan]),
                "added_nodes": sum(len(result.delta.added_nodes) for result in graph_sync_results),
                "removed_nodes": sum(len(result.delta.removed_nodes) for result in graph_sync_results),
            },
        )
        self._transition(
            ExecutionState.CASCADE_UPDATE,
            event_callback,
            "Planner is executing graph-aware cascade updates for impacted backend and QA nodes.",
        )

        planner = CascadePlanner(self.store)
        blast_radius = planner.blast_radius(changed_node_id)
        cascade_plan = planner.execution_plan(changed_node_id)
        backend_result, cascade_sync_results = self._execute_cascade_plan(
            cascade_plan,
            requirement=requirement,
            schemas=schemas,
            apis=apis,
            generated_tests=generated_tests,
            backend_result=backend_result,
            event_callback=event_callback,
        )
        cascade_order = cascade_plan.ordered_node_ids
        context_slice = planner.context_slice(changed_node_id, max_hops=1)
        self._emit(
            event_callback,
            stage="cascade",
            status="completed",
            state=self.state_machine.state.value,
            message="Cascade update batches finished and the focused graph slice is ready for final verification.",
            details={
                "blast_radius": blast_radius,
                "batches": cascade_plan.batches,
            },
        )
        self._transition(
            ExecutionState.VERIFICATION,
            event_callback,
            "Running final verification tests against the generated backend module.",
        )
        self._emit(
            event_callback,
            stage="tests",
            status="started",
            state=self.state_machine.state.value,
            message="Executing verification suite for the generated backend module.",
            details={"targets": test_targets},
        )
        verification_result = self.executor.run_tests(
            test_targets,
            cwd=self.workspace_root,
            container=self._test_execution_config(),
        )
        if not verification_result.success:
            self._emit(
                event_callback,
                stage="tests",
                status="failed",
                state=self.state_machine.state.value,
                message="Verification failed. Rolling the workspace back to the latest safe checkpoint.",
                details={
                    "command": verification_result.command,
                    "returncode": verification_result.returncode,
                },
            )
            self._transition(
                ExecutionState.ROLLBACK,
                event_callback,
                "Rollback manager is restoring the workspace after a failed verification pass.",
            )
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
        self._emit(
            event_callback,
            stage="tests",
            status="completed",
            state=self.state_machine.state.value,
            message="Verification suite passed and the generated backend module is accepted.",
            details={
                "command": verification_result.command,
                "returncode": verification_result.returncode,
            },
        )
        self._transition(
            ExecutionState.DONE,
            event_callback,
            "Run completed successfully. Generated backend artifacts are ready to inspect.",
        )
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
                qa_result.role: {
                    **qa_result.artifacts,
                    "test_expectations": {
                        test.name: test.expected_state for test in qa_result.tests
                    },
                },
                backend_result.role: backend_result.artifacts,
                devops_result.role: devops_result.artifacts,
            },
            workspace_root=str(self.workspace_root),
            graph_path=str(self.graph_path),
            red_test_output=red_test_output,
            verification_output=verification_result.output,
        )

    def _ensure_llm_client(self) -> None:
        if self.llm_client is not None:
            return
        if os.getenv("CODEINGME_ENABLE_LLM") == "1":
            self.llm_client = RelayLLMClient.from_env()
        if self.llm_client is None:
            raise RuntimeError(
                "LLM-only generation requires a configured llm_client or "
                "CODEINGME_ENABLE_LLM=1 with valid CODEINGME_LLM_* settings."
            )

    def _requirement_spec(self, requirement_text: str) -> RequirementSpec:
        return RequirementSpec(
            title=requirement_text,
            summary=requirement_text,
            acceptance_criteria=["Generate contracts", "Drive red-to-green flow", "Propagate impacted changes"],
        )

    def _prime_state_machine(self, current_state: ExecutionState) -> None:
        self.state_machine = StateMachine()
        order = [
            ExecutionState.INTAKE,
            ExecutionState.CONTRACT_GENERATION,
            ExecutionState.TEST_RED,
            ExecutionState.IMPLEMENTATION_LOOP,
            ExecutionState.GRAPH_SYNC,
            ExecutionState.CASCADE_UPDATE,
            ExecutionState.VERIFICATION,
        ]
        if current_state not in order:
            return
        for state in order[1 : order.index(current_state) + 1]:
            self.state_machine.transition(state)

    def _architect_artifacts_from_contracts(
        self,
        schemas: list[DataSchema],
        apis: list[APISpec],
    ) -> dict[str, object]:
        if not schemas or not apis:
            return {}
        schema = schemas[0]
        api = apis[0]
        return {
            "openapi": f"{api.method} {api.route}",
            "schema": f"{schema.name}: {', '.join(f'{key}:{value}' for key, value in schema.fields.items())}",
            "generation_mode": "llm",
        }

    def _qa_artifacts_from_tests(self, tests: list[TestSpec]) -> dict[str, object]:
        test_file = tests[0].path if tests else None
        return {
            "test_file": test_file,
            "generation_mode": "llm",
            "test_expectations": {
                test.name: test.expected_state for test in tests
            },
        }

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
        cleanup_targets = [
            ".dockerignore",
            "Dockerfile",
            "demo_app/__init__.py",
            "docker-compose.yml",
        ]
        for path in self.workspace_root.glob("demo_app/*_api.py"):
            cleanup_targets.append(path.relative_to(self.workspace_root).as_posix())
        for path in self.workspace_root.glob("demo_app/static/*_list.html"):
            cleanup_targets.append(path.relative_to(self.workspace_root).as_posix())
        for path in self.workspace_root.glob("tests_generated/test_*_demo.py"):
            cleanup_targets.append(path.relative_to(self.workspace_root).as_posix())
        cleanup_plan = FilePatchPlan(
            name="cleanup",
            patches=[
                FilePatch(path=path, operation=FilePatchOperation.DELETE)
                for path in sorted(dict.fromkeys(cleanup_targets))
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
        context: AgentContext,
        backend_result: object,
    ) -> None:
        plan = generation_plan(context)
        backend_content = self._content_for_path(plan.backend_module_path, [backend_result.file_plan])

        schema = context.schemas[0]
        api = context.apis[0]
        schema_node_id = self._schema_node_id(schema)
        api_node_id = self._api_node_id(api)
        self.store.upsert_node(
            GraphNode(
                node_id=schema_node_id,
                kind=NodeKind.DATA_MODEL,
                name=schema.name,
                summary=", ".join(f"{key}:{value}" for key, value in schema.fields.items()),
                source=self._source_location(plan.backend_module_path, backend_content, "_items = ["),
            )
        )
        self.store.upsert_node(
            GraphNode(
                node_id=api_node_id,
                kind=NodeKind.API_ROUTE,
                name=f"{api.method} {api.route}",
                summary=api.summary,
                source=self._source_location(plan.backend_module_path, backend_content, api.route),
            )
        )
        self.store.add_edge(GraphEdge(api_node_id, schema_node_id, GraphEdgeType.DEPENDS_ON))

    def _execute_cascade_plan(
        self,
        cascade_plan: CascadePlan,
        *,
        requirement: RequirementSpec,
        schemas: list[object],
        apis: list[object],
        generated_tests: list[object],
        backend_result: AgentResult,
        event_callback: EventCallback | None = None,
    ) -> tuple[AgentResult, list[object]]:
        latest_backend_result = backend_result
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
            self._emit(
                event_callback,
                stage="cascade_batch",
                status="started",
                state=self.state_machine.state.value,
                batch=batch_index,
                message=f"Starting cascade batch {batch_index}.",
                details={"nodes": batch},
            )

            role_context_node_ids: dict[str, set[str]] = {}
            role_order: list[str] = []
            for task in batch_tasks:
                if task.role not in {"backend", "qa"}:
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
                result = self._run_agent(
                    role,
                    context,
                    lambda role=role, context=context: self._run_cascade_role(role, context, generated_tests),
                    event_callback,
                    start_message=f"{role.capitalize()} agent is re-running inside cascade batch {batch_index}.",
                    batch=batch_index,
                )
                role_results[role] = result
                role_plans.append(result.file_plan)

            if "backend" in role_results:
                latest_backend_result = role_results["backend"]

            batch_plan = self._combine_plans(f"cascade_batch_{batch_index}", role_plans)
            batch_plan = self._compact_plan_for_apply(batch_plan)
            if batch_plan is None or not batch_plan.patches:
                self._emit(
                    event_callback,
                    stage="cascade_batch",
                    status="completed",
                    state=self.state_machine.state.value,
                    batch=batch_index,
                    message=f"Cascade batch {batch_index} produced no workspace patches.",
                    details={"roles": role_order},
                )
                continue

            self._apply_and_checkpoint(batch_plan.name, batch_plan)
            graph_sync_results.extend(self._sync_generated_python_files([batch_plan]))
            self._sync_runtime_nodes(context, latest_backend_result)
            self._emit(
                event_callback,
                stage="cascade_batch",
                status="completed",
                state=self.state_machine.state.value,
                batch=batch_index,
                message=f"Cascade batch {batch_index} patches were applied and synced back into the graph.",
                details={
                    "roles": role_order,
                    "patch_count": self._patch_count(batch_plan),
                },
            )

        return latest_backend_result, graph_sync_results

    def _run_cascade_role(
        self,
        role: str,
        context: AgentContext,
        generated_tests: list[object],
    ) -> AgentResult:
        if role == "backend":
            return self.backend.run(context)
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

    def _emit(
        self,
        event_callback: EventCallback | None,
        *,
        stage: str,
        status: str,
        message: str,
        state: str | None = None,
        role: str | None = None,
        batch: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if event_callback is None:
            return
        event_callback(
            OrchestrationEvent(
                stage=stage,
                status=status,
                message=message,
                state=state,
                role=role,
                batch=batch,
                details=details or {},
            )
        )

    def _transition(
        self,
        new_state: ExecutionState,
        event_callback: EventCallback | None,
        message: str,
    ) -> None:
        self.state_machine.transition(new_state)
        self._emit(
            event_callback,
            stage="state",
            status="active",
            state=self.state_machine.state.value,
            message=message,
        )

    def _run_agent(
        self,
        role: str,
        context: AgentContext,
        runner: Callable[[], AgentResult],
        event_callback: EventCallback | None,
        *,
        start_message: str,
        batch: int | None = None,
    ) -> AgentResult:
        self._emit(
            event_callback,
            stage="agent",
            status="started",
            state=self.state_machine.state.value,
            role=role,
            batch=batch,
            message=start_message,
            details={"context_node_ids": sorted(context.graph_slice.node_ids())},
        )
        result = runner()
        self._emit(
            event_callback,
            stage="agent",
            status="completed",
            state=self.state_machine.state.value,
            role=role,
            batch=batch,
            message=result.summary,
            details={
                "artifact_keys": sorted(result.artifacts.keys()),
                "patch_count": self._patch_count(result.file_plan),
                "file_paths": [
                    patch.path
                    for patch in (result.file_plan.patches if result.file_plan is not None else [])
                ],
                "generation_mode": result.artifacts.get("generation_mode"),
                "patch_diffs": self._patch_diffs(result.file_plan),
                "artifact": result.artifacts,
                "tests": [asdict(test) for test in result.tests],
            },
        )
        return result

    def _patch_count(self, plan: FilePatchPlan | None) -> int:
        return len(plan.patches) if plan is not None else 0

    def _patch_diffs(self, plan: FilePatchPlan | None) -> list[dict[str, object]]:
        if plan is None:
            return []
        patch_diffs: list[dict[str, object]] = []
        for patch in plan.patches:
            target = self.workspace_root / patch.path
            previous_content = target.read_text(encoding="utf-8") if target.exists() else None
            try:
                diff_text = render_patch_unified_diff(patch, previous_content)
            except Exception as exc:
                diff_text = f"Unable to render diff: {exc}"
            patch_diffs.append(
                {
                    "path": patch.path,
                    "operation": patch.operation.value,
                    "diff": diff_text,
                }
            )
        return patch_diffs

    def _python_patch_count(self, plans: list[FilePatchPlan | None]) -> int:
        count = 0
        for plan in plans:
            if plan is None:
                continue
            for patch in plan.patches:
                if patch.path.endswith(".py"):
                    count += 1
        return count
