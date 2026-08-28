"""Adapter registry: loads adapters by name."""

from __future__ import annotations

from typing import Any

from heval.adapters.base import AdapterInfo, BaseAdapter

# Registry of adapter name -> class
_ADAPTERS: dict[str, type[BaseAdapter]] = {}


def register_adapter(name: str, cls: type[BaseAdapter]) -> None:
    """Register an adapter class."""
    _ADAPTERS[name] = cls


def get_adapter_class(name: str) -> type[BaseAdapter] | None:
    """Get an adapter class by name."""
    # Lazy-load adapters on first access
    if not _ADAPTERS:
        _load_all()
    return _ADAPTERS.get(name)


def list_adapters() -> dict[str, AdapterInfo]:
    """List all registered adapters with their metadata."""
    if not _ADAPTERS:
        _load_all()
    return {name: cls.info() for name, cls in _ADAPTERS.items()}


def create_adapter(
    name: str,
    workdir: str,
    model: Any,
    gateway_url: str | None = None,
    trace_id: str | None = None,
    config: dict[str, Any] | None = None,
) -> BaseAdapter | None:
    """Create an adapter instance by name."""
    cls = get_adapter_class(name)
    if cls is None:
        return None
    return cls(
        workdir=workdir,
        model=model,
        gateway_url=gateway_url,
        trace_id=trace_id,
        config=config,
    )


def _load_all() -> None:
    """Import all adapter modules to trigger registration."""
    # Import here to avoid circular imports
    from heval.adapters import (  # noqa: F401
        claude_code,
        codex,
        omp,
        opencode,
        pi,
    )
