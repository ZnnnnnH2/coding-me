from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from .models import GraphEdge, GraphEdgeType, GraphNode, NodeKind, SourceLocation
from .slice import GraphSlice


class GraphStore:
    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []

    def upsert_node(self, node: GraphNode) -> None:
        self._nodes[node.node_id] = node

    def remove_node(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)
        self._edges = [edge for edge in self._edges if edge.source != node_id and edge.target != node_id]

    def add_edge(self, edge: GraphEdge) -> None:
        if edge not in self._edges:
            self._edges.append(edge)

    def remove_edge(self, edge: GraphEdge) -> None:
        self._edges = [candidate for candidate in self._edges if candidate != edge]

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def nodes(self) -> list[GraphNode]:
        return list(self._nodes.values())

    def edges(self) -> list[GraphEdge]:
        return list(self._edges)

    def edges_from_sources(self, source_ids: set[str]) -> list[GraphEdge]:
        return [edge for edge in self._edges if edge.source in source_ids]

    def reverse_dependencies(self, node_id: str, edge_types: set[GraphEdgeType] | None = None) -> set[str]:
        graph = defaultdict(set)
        for edge in self._edges:
            if edge_types is None or edge.edge_type in edge_types:
                graph[edge.target].add(edge.source)
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            current = stack.pop()
            for parent in graph.get(current, set()):
                if parent not in seen:
                    seen.add(parent)
                    stack.append(parent)
        return seen

    def slice_from(self, node_ids: set[str]) -> GraphSlice:
        nodes = [node for node_id, node in self._nodes.items() if node_id in node_ids]
        edges = [edge for edge in self._edges if edge.source in node_ids and edge.target in node_ids]
        return GraphSlice(nodes=nodes, edges=edges)

    def to_dict(self) -> dict[str, object]:
        return {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "kind": node.kind.value,
                    "name": node.name,
                    "summary": node.summary,
                    "source": (
                        {
                            "file_path": node.source.file_path,
                            "start_line": node.source.start_line,
                            "end_line": node.source.end_line,
                        }
                        if node.source is not None
                        else None
                    ),
                    "attributes": node.attributes,
                }
                for node in self.nodes()
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "edge_type": edge.edge_type.value,
                }
                for edge in self.edges()
            ],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "GraphStore":
        store = cls()
        for raw_node in payload.get("nodes", []):
            source_data = raw_node.get("source")
            source = SourceLocation(**source_data) if source_data is not None else None
            store.upsert_node(
                GraphNode(
                    node_id=raw_node["node_id"],
                    kind=NodeKind(raw_node["kind"]),
                    name=raw_node["name"],
                    summary=raw_node["summary"],
                    source=source,
                    attributes=dict(raw_node.get("attributes", {})),
                )
            )
        for raw_edge in payload.get("edges", []):
            store.add_edge(
                GraphEdge(
                    source=raw_edge["source"],
                    target=raw_edge["target"],
                    edge_type=GraphEdgeType(raw_edge["edge_type"]),
                )
            )
        return store

    def save_json(self, path: Path | str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")

    @classmethod
    def load_json(cls, path: Path | str) -> "GraphStore":
        source = Path(path)
        return cls.from_dict(json.loads(source.read_text(encoding="utf-8")))
