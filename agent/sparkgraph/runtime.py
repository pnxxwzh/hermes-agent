"""SparkGraph runtime health and probe helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict

import requests


@dataclass(frozen=True)
class RuntimeHealth:
    """Lightweight runtime health snapshot used by future bridges."""

    enabled: bool
    healthy: bool
    degraded: bool = False
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SparkGraphRuntimeSnapshot:
    """Aggregated SparkGraph runtime health."""

    healthy: bool
    degraded: bool
    embedding: RuntimeHealth


def _normalized_models_url(base_url: str) -> str:
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return ""
    if normalized.endswith("/models"):
        return normalized
    if normalized.endswith("/v1"):
        return normalized + "/models"
    return normalized + "/v1/models"


def _probe_models_endpoint(*, base_url: str, api_key: str, timeout: int) -> tuple[bool, dict[str, Any]]:
    url = _normalized_models_url(base_url)
    if not url:
        return False, {"reason": "missing_base_url"}

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        response = requests.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return False, {"reason": "probe_failed", "error": str(exc), "probed_url": url}

    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        models = [str(item.get("id", "")).strip() for item in payload["data"] if isinstance(item, dict)]
        return True, {"models": [m for m in models if m], "probed_url": url}
    if isinstance(payload, list):
        models = [str(item.get("id", "")).strip() for item in payload if isinstance(item, dict)]
        return True, {"models": [m for m in models if m], "probed_url": url}
    return True, {"models": [], "probed_url": url}


def _component_health(
    *,
    enabled: bool,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    timeout: int,
    component_name: str,
    probe_enabled: bool = True,
    probe_fn: Callable[..., tuple[bool, dict[str, Any]]] | None = None,
) -> RuntimeHealth:
    if not enabled:
        return RuntimeHealth(
            enabled=False,
            healthy=False,
            degraded=False,
            reason=f"{component_name} disabled",
        )

    configured_fields = {
        "provider": bool(provider),
        "model": bool(model),
        "base_url": bool(base_url),
    }
    configured_count = sum(1 for value in configured_fields.values() if value)
    if configured_count == 0:
        return RuntimeHealth(
            enabled=False,
            healthy=False,
            degraded=False,
            reason=f"{component_name} not configured",
        )
    if configured_count < len(configured_fields):
        return RuntimeHealth(
            enabled=True,
            healthy=False,
            degraded=True,
            reason=f"{component_name} partially configured",
            details={"configured_fields": configured_fields},
        )

    if not probe_enabled:
        return RuntimeHealth(
            enabled=True,
            healthy=True,
            degraded=False,
            reason=f"{component_name} configured",
            details={"probe_skipped": True},
        )

    probe = probe_fn or _probe_models_endpoint
    ok, details = probe(base_url=base_url, api_key=api_key, timeout=timeout)
    return RuntimeHealth(
        enabled=True,
        healthy=ok,
        degraded=not ok,
        reason="" if ok else f"{component_name} probe failed",
        details=details,
    )


def embedding_runtime_health(
    config,
    probe_fn: Callable[..., tuple[bool, dict[str, Any]]] | None = None,
    *,
    probe_enabled: bool = True,
) -> RuntimeHealth:
    return _component_health(
        enabled=bool(getattr(getattr(config, "recall", None), "enabled", True)),
        provider=config.embedding.provider,
        model=config.embedding.model,
        base_url=config.embedding.base_url,
        api_key=config.embedding.api_key,
        timeout=config.embedding.timeout,
        component_name="embedding runtime",
        probe_enabled=probe_enabled,
        probe_fn=probe_fn,
    )


def build_runtime_snapshot(
    config,
    probe_fn: Callable[..., tuple[bool, dict[str, Any]]] | None = None,
    *,
    probe_enabled: bool = True,
) -> SparkGraphRuntimeSnapshot:
    embedding = embedding_runtime_health(config, probe_fn=probe_fn, probe_enabled=probe_enabled)
    degraded = embedding.degraded
    healthy = not degraded
    return SparkGraphRuntimeSnapshot(
        healthy=healthy,
        degraded=degraded,
        embedding=embedding,
    )
