"""定义图节点、边与增量结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class NodeKind(StrEnum):
    REQUIREMENT = "requirement"
    API_ROUTE = "api_route"
    SERVICE = "service"
    DATA_MODEL = "data_model"
    TEST_CASE = "test_case"
    ARTIFACT = "artifact"


class GraphEdgeType(StrEnum):
    IMPLEMENTS = "implements"
    READS = "reads"
    WRITES = "writes"
    VERIFIES = "verifies"
    DEPENDS_ON = "depends_on"
    GENERATES = "generates"


@dataclass(slots=True)
class SourceLocation:
    file_path: str
    start_line: int = 1
    end_line: int = 1


@dataclass(slots=True)
class GraphNode:
    node_id: str
    kind: NodeKind
    name: str
    summary: str
    source: SourceLocation | None = None
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class GraphEdge:
    source: str
    target: str
    edge_type: GraphEdgeType


@dataclass(slots=True)
class GraphDelta:
    added_nodes: list[str] = field(default_factory=list)
    updated_nodes: list[str] = field(default_factory=list)
    removed_nodes: list[str] = field(default_factory=list)
    added_edges: list[tuple[str, str, str]] = field(default_factory=list)
    removed_edges: list[tuple[str, str, str]] = field(default_factory=list)
