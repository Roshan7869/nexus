import sqlite3
import time
from collections import defaultdict
from typing import NamedTuple


class Edge(NamedTuple):
    target_id: int
    weight: float
    edge_type: str


class WorkflowGraph:
    def __init__(self, db_path: str):
        self._db_path = db_path
        self._outgoing: defaultdict[int, list[Edge]] = defaultdict(list)
        self._incoming: defaultdict[int, list[Edge]] = defaultdict(list)
        self._loaded_at: float = 0.0
        self._edge_count: int = 0
        self._node_count: int = 0
        self.load_from_db()

    def incoming_edges(self, resource_id: int) -> list[Edge]:
        return list(self._incoming.get(resource_id, []))

    def outgoing_edges(self, resource_id: int) -> list[Edge]:
        return list(self._outgoing.get(resource_id, []))

    def incoming_count(self, resource_id: int) -> int:
        return len(self._incoming.get(resource_id, []))

    def sources(self, resource_id: int) -> list[int]:
        return [e.target_id for e in self._incoming.get(resource_id, [])]

    def targets(self, resource_id: int) -> list[int]:
        return [e.target_id for e in self._outgoing.get(resource_id, [])]

    def load_from_db(self) -> None:
        self._outgoing.clear()
        self._incoming.clear()
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT source_id, target_id, weight, edge_type FROM resource_edges"
        ).fetchall()
        conn.close()
        nodes: set[int] = set()
        for row in rows:
            source_id = row["source_id"]
            target_id = row["target_id"]
            weight = row["weight"] if row["weight"] is not None else 1.0
            edge_type = row["edge_type"] if row["edge_type"] is not None else "co_triggered"
            edge = Edge(target_id=target_id, weight=weight, edge_type=edge_type)
            self._outgoing[source_id].append(edge)
            edge_in = Edge(target_id=source_id, weight=weight, edge_type=edge_type)
            self._incoming[target_id].append(edge_in)
            nodes.add(source_id)
            nodes.add(target_id)
        self._edge_count = len(rows)
        self._node_count = len(nodes)
        self._loaded_at = time.time()

    def edge_count(self) -> int:
        return self._edge_count

    def node_count(self) -> int:
        return self._node_count

    def refresh(self, interval: int = 3600) -> None:
        if time.time() - self._loaded_at > interval:
            self.load_from_db()


class EdgeCountCache:
    def __init__(self) -> None:
        self._cache: dict[int, int] = {}

    def get(self, resource_id: int) -> int:
        return self._cache.get(resource_id, 0)

    def rebuild_from(self, graph: WorkflowGraph) -> None:
        self._cache.clear()
        for rid in set(list(graph._outgoing.keys()) + list(graph._incoming.keys())):
            self._cache[rid] = graph.incoming_count(rid)

    def __contains__(self, resource_id: int) -> bool:
        return resource_id in self._cache

    def __len__(self) -> int:
        return len(self._cache)
