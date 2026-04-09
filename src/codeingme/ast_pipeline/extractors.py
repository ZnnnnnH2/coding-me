"""从 Python AST 中提取图节点与依赖关系。"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from codeingme.ast_pipeline.parser import ParsedModule
from codeingme.ast_pipeline.types import ExtractedGraph
from codeingme.graph import GraphEdge, GraphEdgeType, GraphNode, NodeKind, SourceLocation


_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}
_FUNCTION_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(slots=True)
class _DefinitionRecord:
    node_id: str
    kind: NodeKind
    name: str
    qualname: str
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


class PythonEntityExtractor:
    def extract(self, module: ParsedModule) -> ExtractedGraph:
        definitions: list[_DefinitionRecord] = []
        nodes: list[GraphNode] = []
        self._collect_definitions(module.file_path, module.tree.body, "", definitions, nodes)
        direct_symbol_ids = {
            record.name: record.node_id
            for record in definitions
            if "." not in record.qualname
        }
        service_instances = self._service_instances(module.tree.body, definitions)
        edges = self._extract_edges(definitions, direct_symbol_ids, service_instances)
        return ExtractedGraph(nodes=nodes, edges=edges)

    def _collect_definitions(
        self,
        file_path: str,
        statements: list[ast.stmt],
        prefix: str,
        definitions: list[_DefinitionRecord],
        nodes: list[GraphNode],
    ) -> None:
        for statement in statements:
            if isinstance(statement, _FUNCTION_TYPES):
                qualname = self._qualname(prefix, statement.name)
                node = self._function_node(file_path, statement, qualname)
                nodes.append(node)
                definitions.append(
                    _DefinitionRecord(
                        node_id=node.node_id,
                        kind=node.kind,
                        name=statement.name,
                        qualname=qualname,
                        node=statement,
                    )
                )
                self._collect_definitions(file_path, statement.body, qualname, definitions, nodes)
                continue
            if isinstance(statement, ast.ClassDef):
                qualname = self._qualname(prefix, statement.name)
                node = self._class_node(file_path, statement, qualname)
                nodes.append(node)
                definitions.append(
                    _DefinitionRecord(
                        node_id=node.node_id,
                        kind=node.kind,
                        name=statement.name,
                        qualname=qualname,
                        node=statement,
                    )
                )
                self._collect_definitions(file_path, statement.body, qualname, definitions, nodes)

    def _extract_edges(
        self,
        definitions: list[_DefinitionRecord],
        direct_symbol_ids: dict[str, str],
        service_instances: dict[str, str],
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for record in definitions:
            if isinstance(record.node, ast.ClassDef):
                continue
            route_ids = self._route_contract_ids(record.node)
            if route_ids:
                for route_id in route_ids:
                    edges.append(GraphEdge(record.node_id, route_id, GraphEdgeType.IMPLEMENTS))
                    edges.extend(
                        self._service_implementation_edges(record.node, route_id, service_instances)
                    )
            if record.kind is NodeKind.TEST_CASE:
                edges.extend(self._test_verification_edges(record.node, record.node_id))
            edges.extend(self._local_dependency_edges(record.node, record.node_id, direct_symbol_ids))
        unique_edges: list[GraphEdge] = []
        seen: set[tuple[str, str, str]] = set()
        for edge in edges:
            edge_key = (edge.source, edge.target, edge.edge_type.value)
            if edge_key in seen:
                continue
            seen.add(edge_key)
            unique_edges.append(edge)
        return unique_edges

    def _service_instances(
        self,
        statements: list[ast.stmt],
        definitions: list[_DefinitionRecord],
    ) -> dict[str, str]:
        service_class_ids = {
            record.name: record.node_id
            for record in definitions
            if record.kind is NodeKind.SERVICE and "." not in record.qualname
        }
        instances: dict[str, str] = {}
        for statement in statements:
            value = None
            targets: list[ast.expr] = []
            if isinstance(statement, ast.Assign):
                value = statement.value
                targets = statement.targets
            elif isinstance(statement, ast.AnnAssign):
                value = statement.value
                targets = [statement.target]
            if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Name):
                continue
            service_node_id = service_class_ids.get(value.func.id)
            if service_node_id is None:
                continue
            for target in targets:
                if isinstance(target, ast.Name):
                    instances[target.id] = service_node_id
        return instances

    def _service_implementation_edges(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        route_id: str,
        service_instances: dict[str, str],
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Call):
                continue
            if not isinstance(candidate.func, ast.Attribute):
                continue
            if not isinstance(candidate.func.value, ast.Name):
                continue
            service_node_id = service_instances.get(candidate.func.value.id)
            if service_node_id is None:
                continue
            edges.append(GraphEdge(service_node_id, route_id, GraphEdgeType.IMPLEMENTS))
        return edges

    def _test_verification_edges(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        node_id: str,
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Call):
                continue
            method_path = self._http_call(candidate)
            if method_path is None:
                continue
            method, path = method_path
            edges.append(GraphEdge(node_id, self._api_contract_id(method, path), GraphEdgeType.VERIFIES))
        return edges

    def _local_dependency_edges(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        node_id: str,
        direct_symbol_ids: dict[str, str],
    ) -> list[GraphEdge]:
        edges: list[GraphEdge] = []
        for candidate in ast.walk(node):
            if not isinstance(candidate, ast.Call):
                continue
            if not isinstance(candidate.func, ast.Name):
                continue
            target_id = direct_symbol_ids.get(candidate.func.id)
            if target_id is None or target_id == node_id:
                continue
            edges.append(GraphEdge(node_id, target_id, GraphEdgeType.DEPENDS_ON))
        return edges

    def _function_node(
        self,
        file_path: str,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        qualname: str,
    ) -> GraphNode:
        kind = NodeKind.TEST_CASE if node.name.startswith("test_") else NodeKind.ARTIFACT
        return GraphNode(
            node_id=self._node_id(file_path, "function", qualname),
            kind=kind,
            name=node.name,
            summary=f"Function extracted from AST: {node.name}",
            source=SourceLocation(
                file_path=file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            ),
            attributes={
                "symbol_type": "function",
                "symbol_name": node.name,
                "qualname": qualname,
            },
        )

    def _class_node(self, file_path: str, node: ast.ClassDef, qualname: str) -> GraphNode:
        kind = NodeKind.SERVICE if node.name.endswith("Service") else NodeKind.ARTIFACT
        return GraphNode(
            node_id=self._node_id(file_path, "class", qualname),
            kind=kind,
            name=node.name,
            summary=f"Class extracted from AST: {node.name}",
            source=SourceLocation(
                file_path=file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno),
            ),
            attributes={
                "symbol_type": "class",
                "symbol_name": node.name,
                "qualname": qualname,
            },
        )

    def _route_contract_ids(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
        route_ids: list[str] = []
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            method = decorator.func.attr.lower()
            if method not in _HTTP_METHODS or not decorator.args:
                continue
            path = self._string_literal(decorator.args[0])
            if path is None:
                continue
            route_ids.append(self._api_contract_id(method, path))
        return route_ids

    def _http_call(self, node: ast.Call) -> tuple[str, str] | None:
        if not node.args:
            return None
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr.lower()
            if method not in _HTTP_METHODS:
                return None
            if not isinstance(node.func.value, ast.Name):
                return None
            if not node.func.value.id.lower().endswith("client"):
                return None
        elif isinstance(node.func, ast.Name):
            if node.func.id not in {"_get_json", "_get_text", "get_json", "get_text"}:
                return None
            method = "get"
        else:
            return None
        path = self._string_literal(node.args[0])
        if path is None:
            return None
        return method, path

    @staticmethod
    def _string_literal(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        return None

    @staticmethod
    def _qualname(prefix: str, name: str) -> str:
        if not prefix:
            return name
        return f"{prefix}.{name}"

    @staticmethod
    def _api_contract_id(method: str, path: str) -> str:
        return f"api:{method}:{path}"

    @staticmethod
    def _node_id(file_path: str, symbol_type: str, symbol_name: str) -> str:
        return f"{file_path}::{symbol_type}:{symbol_name}"
