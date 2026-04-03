"""SparkGraph manager skeleton.

The manager grows into the orchestration point for store, flush integration,
maintenance, and recall. In P2-B1 it only owns validated config and directory
bootstrap helpers.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.sparkgraph.config import SparkGraphConfig, parse_sparkgraph_config
from agent.sparkgraph.db import ensure_db_parent
from agent.sparkgraph.formatter import build_recall_payload
from agent.sparkgraph.recaller import RecallConfig, recall_nodes
from agent.sparkgraph.runtime import SparkGraphRuntimeSnapshot, build_runtime_snapshot
from agent.sparkgraph.store import SparkGraphStore


@dataclass
class SparkGraphManager:
    config: SparkGraphConfig
    store: SparkGraphStore | None = None

    @classmethod
    def from_raw_config(cls, raw_config, hermes_home=None) -> "SparkGraphManager":
        config = parse_sparkgraph_config(raw_config, hermes_home=hermes_home)
        return cls(config=config)

    def bootstrap_dirs(self):
        ensure_db_parent(self.config)

    def ensure_store(self) -> SparkGraphStore:
        if self.store is None:
            self.bootstrap_dirs()
            self.store = SparkGraphStore.from_config(self.config)
        return self.store

    def build_recall_block(
        self,
        query: str,
        *,
        max_nodes: int | None = None,
        max_chars: int | None = None,
    ) -> tuple[str, int]:
        if not self.config.recall.enabled:
            return "", 0
        store = self.ensure_store()
        recall_cfg = self.config.recall
        nodes, edges, token_estimate = recall_nodes(
            store,
            query=query,
            config=RecallConfig(
                max_nodes=max_nodes if max_nodes is not None else recall_cfg.max_items,
                related_limit=recall_cfg.max_related,
            ),
            embedding_config=self.config.embedding,
        )
        block, included_ids = build_recall_payload(
            nodes,
            edges=edges,
            max_chars=max_chars if max_chars is not None else recall_cfg.max_chars,
        )
        if block and included_ids:
            try:
                store.mark_recalled(included_ids)
            except Exception:
                pass
        return block, token_estimate

    def runtime_snapshot(self, *, probe_fn=None, probe_enabled: bool = True) -> SparkGraphRuntimeSnapshot:
        return build_runtime_snapshot(self.config, probe_fn=probe_fn, probe_enabled=probe_enabled)
