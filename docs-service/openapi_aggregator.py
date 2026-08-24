"""Fetch and merge OpenAPI documents from the three runtime services."""

from __future__ import annotations

import asyncio
import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx


HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
OMITTED_SERVICE_PATHS = {"/health", "/ready"}


@dataclass(frozen=True)
class ServiceSpec:
    key: str
    title: str
    base_url: str
    description: str
    schema_url: str | None = None

    @property
    def openapi_url(self) -> str:
        return self.schema_url or f"{self.base_url}/openapi.json"


def service_specs_from_env() -> tuple[ServiceSpec, ...]:
    return (
        ServiceSpec(
            key="vm_manager",
            title="VM Manager",
            base_url=os.getenv(
                "AIVIRTEACH_VM_DOCS_URL", "http://127.0.0.1:8760"
            ).rstrip("/"),
            description="Privileged VM lifecycle and browser-session operations.",
            schema_url=os.getenv(
                "AIVIRTEACH_VM_OPENAPI_URL",
                "http://127.0.0.1:8760/openapi.json",
            ),
        ),
        ServiceSpec(
            key="diagnostic_gateway",
            title="Diagnostic Gateway",
            base_url=os.getenv(
                "AIVIRTEACH_DIAGNOSTIC_DOCS_URL", "http://127.0.0.1:8765"
            ).rstrip("/"),
            description="Fixed-template, read-only observations through QEMU Guest Agent.",
            schema_url=os.getenv(
                "AIVIRTEACH_DIAGNOSTIC_OPENAPI_URL",
                "http://127.0.0.1:8765/openapi.json",
            ),
        ),
        ServiceSpec(
            key="agent",
            title="Agent Service",
            base_url=os.getenv(
                "AIVIRTEACH_AGENT_DOCS_URL", "http://127.0.0.1:8770"
            ).rstrip("/"),
            description="Course-aware diagnosis backed by the read-only gateway.",
            schema_url=os.getenv(
                "AIVIRTEACH_AGENT_OPENAPI_URL",
                "http://127.0.0.1:8770/openapi.json",
            ),
        ),
    )


def _fetch_timeout() -> float:
    raw = os.getenv("AIVIRTEACH_DOCS_FETCH_TIMEOUT", "3")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError("AIVIRTEACH_DOCS_FETCH_TIMEOUT must be a number") from exc
    if not 0.2 <= value <= 30:
        raise ValueError("AIVIRTEACH_DOCS_FETCH_TIMEOUT must be between 0.2 and 30")
    return value


def _rewrite_schema_refs(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _rewrite_schema_refs(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_rewrite_schema_refs(item, mapping) for item in value]
    if isinstance(value, str):
        prefix = "#/components/schemas/"
        if value.startswith(prefix):
            name = value[len(prefix) :]
            return f"{prefix}{mapping.get(name, name)}"
    return value


def _namespace_schemas(document: dict[str, Any], service_key: str) -> dict[str, Any]:
    cloned = deepcopy(document)
    schemas = cloned.get("components", {}).get("schemas", {})
    mapping = {name: f"{service_key}_{name}" for name in schemas}
    cloned = _rewrite_schema_refs(cloned, mapping)
    if schemas:
        cloned.setdefault("components", {})["schemas"] = {
            mapping[name]: _rewrite_schema_refs(schema, mapping)
            for name, schema in schemas.items()
        }
    return cloned


def _operations(path_item: dict[str, Any]):
    for method, operation in path_item.items():
        if method in HTTP_METHODS and isinstance(operation, dict):
            yield method, operation


def merge_documents(
    service_documents: list[tuple[ServiceSpec, dict[str, Any]]],
    *,
    statuses: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Merge service schemas without mounting or proxying service operations."""

    merged: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "AIVirTeach Labs API",
            "version": "2.0.0",
            "description": (
                "Unified reference for three independently hosted services. "
                "Each operation declares the service address that executes it."
            ),
        },
        "paths": {},
        "components": {},
        "tags": [],
        "x-aivirteach-services": {
            spec.key: spec.base_url for spec, _ in service_documents
        },
        "x-aivirteach-service-status": statuses or {},
    }
    seen_tags: set[str] = set()

    for spec, original_document in service_documents:
        document = _namespace_schemas(original_document, spec.key)
        for section, values in document.get("components", {}).items():
            target = merged["components"].setdefault(section, {})
            for name, value in values.items():
                if name in target and target[name] != value:
                    raise ValueError(
                        f"Conflicting OpenAPI component {section}/{name} from {spec.key}"
                    )
                target.setdefault(name, value)

        for tag in document.get("tags", []):
            name = tag.get("name") if isinstance(tag, dict) else None
            if isinstance(name, str) and name not in seen_tags:
                merged["tags"].append(deepcopy(tag))
                seen_tags.add(name)

        for path, original_item in document.get("paths", {}).items():
            if path in OMITTED_SERVICE_PATHS:
                continue
            path_item = deepcopy(original_item)
            target_item = merged["paths"].setdefault(path, {})
            for method, operation in _operations(path_item):
                if method in target_item:
                    raise ValueError(
                        f"Conflicting OpenAPI operation {method.upper()} {path}"
                    )
                operation["servers"] = [
                    {
                        "url": spec.base_url,
                        "description": spec.title,
                    }
                ]
                operation_id = operation.get("operationId", f"{method}_{path}")
                operation["operationId"] = f"{spec.key}_{operation_id}"
                target_item[method] = operation

            for name, value in path_item.items():
                if name not in HTTP_METHODS:
                    target_item.setdefault(name, value)

    merged["components"] = {
        section: values
        for section, values in merged["components"].items()
        if values
    }
    return merged


async def _fetch_document(
    client: httpx.AsyncClient, spec: ServiceSpec
) -> dict[str, Any]:
    response = await client.get(spec.openapi_url)
    response.raise_for_status()
    document = response.json()
    if not isinstance(document, dict) or not isinstance(document.get("paths"), dict):
        raise ValueError(f"{spec.key} returned an invalid OpenAPI document")
    return document


_document_cache: dict[str, dict[str, Any]] = {}
_cache_lock = asyncio.Lock()


async def build_unified_document(
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    specs = service_specs_from_env()
    owns_client = client is None
    active_client = client or httpx.AsyncClient(timeout=_fetch_timeout())
    try:
        results = await asyncio.gather(
            *(_fetch_document(active_client, spec) for spec in specs),
            return_exceptions=True,
        )
    finally:
        if owns_client:
            await active_client.aclose()

    documents: list[tuple[ServiceSpec, dict[str, Any]]] = []
    statuses: dict[str, dict[str, str]] = {}
    async with _cache_lock:
        for spec, result in zip(specs, results, strict=True):
            if isinstance(result, Exception):
                cached = _document_cache.get(spec.key)
                if cached is None:
                    statuses[spec.key] = {
                        "state": "unavailable",
                        "detail": type(result).__name__,
                    }
                    continue
                statuses[spec.key] = {
                    "state": "stale",
                    "detail": type(result).__name__,
                }
                documents.append((spec, cached))
                continue

            _document_cache[spec.key] = result
            statuses[spec.key] = {"state": "live"}
            documents.append((spec, result))

    document = merge_documents(documents, statuses=statuses)
    document["x-aivirteach-services"] = {
        spec.key: spec.base_url for spec in specs
    }
    return document
