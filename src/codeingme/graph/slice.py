"""定义图切片对象。"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import GraphEdge, GraphNode


@dataclass(slots=True)
class GraphSlice:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def node_ids(self) -> set[str]:
        return {node.node_id for node in self.nodes}
