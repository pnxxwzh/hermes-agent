"""SparkGraph configuration helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from hermes_constants import get_hermes_home


DEFAULT_SPARKGRAPH_CONFIG: Dict[str, Any] = {
    "mode": "flush_integrated",
    "db_path": "",
    "recall": {
        "enabled": True,
        "max_items": 4,
        "max_related": 4,
        "max_chars": 1800,
    },
    "embedding": {
        # Embeddings are opt-in. Leaving these blank avoids surprise network
        # calls on fresh installs where no embedding runtime is configured.
        "provider": "",
        "model": "",
        "base_url": "",
        "api_key": "",
        "timeout": 20,
    },
}


class SparkGraphConfigError(ValueError):
    """Raised when SparkGraph config is invalid."""


@dataclass(frozen=True)
class SparkGraphRecallConfig:
    enabled: bool = True
    max_items: int = 4
    max_related: int = 4
    max_chars: int = 1800


@dataclass(frozen=True)
class SparkGraphEmbeddingConfig:
    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    timeout: int = 20


@dataclass(frozen=True)
class SparkGraphConfig:
    mode: str
    db_path: Path
    recall: SparkGraphRecallConfig
    embedding: SparkGraphEmbeddingConfig


def sparkgraph_home(hermes_home: Path | None = None) -> Path:
    """Return the profile-scoped SparkGraph home directory."""
    return (hermes_home or get_hermes_home()) / "sparkgraph"


def resolve_sparkgraph_db_path(db_path: str = "", hermes_home: Path | None = None) -> Path:
    """Resolve the SparkGraph database path inside the active Hermes profile."""
    if db_path and db_path.strip():
        return Path(db_path).expanduser().resolve()
    return sparkgraph_home(hermes_home) / "default.db"


def _require_dict(value: Any, key: str) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SparkGraphConfigError(f"sparkgraph.{key} must be a mapping")
    return dict(value)


def _require_positive_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SparkGraphConfigError(f"sparkgraph.{key} must be a positive integer")
    return value


def _require_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise SparkGraphConfigError(f"sparkgraph.{key} must be a boolean")
    return value


def _require_string(value: Any, key: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SparkGraphConfigError(f"sparkgraph.{key} must be a string")
    return value.strip()


def parse_sparkgraph_config(raw: Dict[str, Any] | None, hermes_home: Path | None = None) -> SparkGraphConfig:
    """Parse a raw SparkGraph config mapping into validated dataclasses."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise SparkGraphConfigError("sparkgraph must be a mapping")

    mode = _require_string(raw.get("mode", DEFAULT_SPARKGRAPH_CONFIG["mode"]), "mode")
    if mode != "flush_integrated":
        raise SparkGraphConfigError("sparkgraph.mode must be 'flush_integrated'")

    recall_raw = _require_dict(raw.get("recall"), "recall")
    recall = SparkGraphRecallConfig(
        enabled=_require_bool(
            recall_raw.get("enabled", DEFAULT_SPARKGRAPH_CONFIG["recall"]["enabled"]),
            "recall.enabled",
        ),
        max_items=_require_positive_int(
            recall_raw.get("max_items", DEFAULT_SPARKGRAPH_CONFIG["recall"]["max_items"]),
            "recall.max_items",
        ),
        max_related=_require_positive_int(
            recall_raw.get("max_related", DEFAULT_SPARKGRAPH_CONFIG["recall"]["max_related"]),
            "recall.max_related",
        ),
        max_chars=_require_positive_int(
            recall_raw.get("max_chars", DEFAULT_SPARKGRAPH_CONFIG["recall"]["max_chars"]),
            "recall.max_chars",
        ),
    )

    embedding_raw = _require_dict(raw.get("embedding"), "embedding")
    use_embedding_defaults = "embedding" not in raw
    embedding_defaults = DEFAULT_SPARKGRAPH_CONFIG["embedding"] if use_embedding_defaults else {}
    embedding = SparkGraphEmbeddingConfig(
        provider=_require_string(
            embedding_raw.get("provider", embedding_defaults.get("provider", "")),
            "embedding.provider",
        ),
        model=_require_string(
            embedding_raw.get("model", embedding_defaults.get("model", "")),
            "embedding.model",
        ),
        base_url=_require_string(
            embedding_raw.get("base_url", embedding_defaults.get("base_url", "")),
            "embedding.base_url",
        ),
        api_key=_require_string(
            embedding_raw.get("api_key", embedding_defaults.get("api_key", "")),
            "embedding.api_key",
        ),
        timeout=_require_positive_int(
            embedding_raw.get("timeout", embedding_defaults.get("timeout", DEFAULT_SPARKGRAPH_CONFIG["embedding"]["timeout"])),
            "embedding.timeout",
        ),
    )

    db_path = resolve_sparkgraph_db_path(
        _require_string(raw.get("db_path", DEFAULT_SPARKGRAPH_CONFIG["db_path"]), "db_path"),
        hermes_home=hermes_home,
    )

    return SparkGraphConfig(
        mode=mode,
        db_path=db_path,
        recall=recall,
        embedding=embedding,
    )
