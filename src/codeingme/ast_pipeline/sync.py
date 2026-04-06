from __future__ import annotations

from codeingme.ast_pipeline.extractors import PythonEntityExtractor
from codeingme.ast_pipeline.parser import PythonModuleParser
from codeingme.ast_pipeline.types import GraphSyncResult
from codeingme.graph import GraphDelta, GraphEdge, GraphEdgeType, GraphStore, GraphNode


class GraphSynchronizer:
    def __init__(self, store: GraphStore) -> None:
        self.store = store
        self.parser = PythonModuleParser()
        self.extractor = PythonEntityExtractor()

    def sync_source(self, source: str, file_path: str = "<memory>") -> GraphSyncResult:
        parsed = self.parser.parse(source, file_path=file_path)
        extracted = self.extractor.extract(parsed)
        extracted_ids = {node.node_id for node in extracted.nodes}
        prior_ids = {node.node_id for node in self.nodes_for_file(file_path)}
        prior_edges = {
            self._edge_key(edge)
            for edge in self.store.edges_from_sources(prior_ids)
        }
        delta = GraphDelta()

        for node in extracted.nodes:
            current = self.store.get_node(node.node_id)
            if current is None:
                delta.added_nodes.append(node.node_id)
            elif current != node:
                delta.updated_nodes.append(node.node_id)
            self.store.upsert_node(node)

        referenced_node_ids = sorted(
            {
                edge.target
                for edge in extracted.edges
                if edge.target not in extracted_ids
            }
        )
        missing_references = sorted(
            node_id for node_id in referenced_node_ids if self.store.get_node(node_id) is None
        )
        extracted_edges = {
            self._edge_key(edge)
            for edge in extracted.edges
            if self.store.get_node(edge.target) is not None
        }
        for edge in extracted.edges:
            edge_key = self._edge_key(edge)
            if edge_key not in extracted_edges:
                continue
            if edge_key not in prior_edges:
                delta.added_edges.append(edge_key)
            self.store.add_edge(edge)

        stale_edges = sorted(prior_edges - extracted_edges)
        for edge_key in stale_edges:
            self.store.remove_edge(self._edge_from_key(edge_key))
            delta.removed_edges.append(edge_key)

        stale_ids = sorted(prior_ids - extracted_ids)
        for node_id in stale_ids:
            self.store.remove_node(node_id)
            delta.removed_nodes.append(node_id)

        return GraphSyncResult(
            delta=delta,
            referenced_node_ids=referenced_node_ids,
            missing_references=missing_references,
            file_path=file_path,
            upserted_node_ids=sorted(node.node_id for node in extracted.nodes),
        )

    def nodes_for_file(self, file_path: str) -> list[GraphNode]:
        return [
            node
            for node in self.store.nodes()
            if node.source is not None
            and node.source.file_path == file_path
            and node.node_id.startswith(f"{file_path}::")
        ]

    @staticmethod
    def _edge_key(edge: GraphEdge) -> tuple[str, str, str]:
        return (edge.source, edge.target, edge.edge_type.value)

    @staticmethod
    def _edge_from_key(edge_key: tuple[str, str, str]) -> GraphEdge:
        source, target, edge_type = edge_key
        return GraphEdge(source=source, target=target, edge_type=GraphEdgeType(edge_type))
