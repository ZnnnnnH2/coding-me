from __future__ import annotations

from dataclasses import dataclass, field

from codeingme.graph import GraphDelta, GraphEdge, GraphNode


@dataclass(slots=True)
class ExtractedGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


@dataclass(slots=True)
class GraphSyncResult:
    delta: GraphDelta
    referenced_node_ids: list[str]
    missing_references: list[str]
    file_path: str = "<memory>"
    upserted_node_ids: list[str] = field(default_factory=list)
