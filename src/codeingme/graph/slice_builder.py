"""负责按节点构造上下文图切片。"""

from __future__ import annotations

from .models import GraphEdgeType
from .query import GraphQueryService
from .slice import GraphSlice
from .store import GraphStore


class GraphSliceBuilder:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.query = GraphQueryService(store)

    def from_node_ids(self, node_ids: set[str]) -> GraphSlice:
        return self.store.slice_from(node_ids)

    def contextual(self, changed_node_id: str, *, max_hops: int = 1, edge_types: set[GraphEdgeType] | None = None) -> GraphSlice:
        return self.query.slice_around(changed_node_id, max_hops=max_hops, edge_types=edge_types)

    def empty(self) -> GraphSlice:
        return GraphSlice()
