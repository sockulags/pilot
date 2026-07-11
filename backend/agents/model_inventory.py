"""Health-checked local model inventory — configured vs installed vs healthy.

The configured registry (``config.OLLAMA_MODELS``) lists the models Pilot *would*
like to use. It is NOT proof that any of them are actually pulled into Ollama.
Routing a turn to a configured-but-missing model fails at call time, so the
front brain must consult what is *installed* before it advertises experts or
picks an answering model.

This module queries Ollama ``/api/tags`` once and returns a structured
:class:`ModelInventory`. The critical property is **fail closed**: if discovery
fails (Ollama down, network error, empty/garbage response) we do NOT assume the
whole registry is installed. Instead the inventory is marked ``discovery_ok =
False`` and exposes an empty installed set, so the coordinator advertises no
unverified experts and routing falls back to the configured default with a
recorded reason. A clear warning is logged.
"""

from __future__ import annotations

import logging
import asyncio
from dataclasses import dataclass, field

import httpx

import model_settings
from config import (
    OLLAMA_CLASSIFIER_NUM_CTX,
    OLLAMA_CODE_NUM_CTX,
    OLLAMA_DEFAULT_NUM_CTX,
    OLLAMA_GATEWAY_NUM_CTX,
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    OLLAMA_SYNTHESIS_NUM_CTX,
    OLLAMA_VISION_MODEL,
    OLLAMA_VISION_NUM_CTX,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelCapabilities:
    """Declared model capability and Pilot's conservative runtime window."""

    declared_context: int | None
    effective_context: int
    effective_contexts: dict[str, int] = field(default_factory=dict)
    tools: bool = False
    thinking: bool = False
    vision: bool = False
    embedding: bool = False


_ROLE_BUDGETS = {
    "classifier": OLLAMA_CLASSIFIER_NUM_CTX,
    "gateway": OLLAMA_GATEWAY_NUM_CTX,
    "vision": OLLAMA_VISION_NUM_CTX,
    "vision_agent": OLLAMA_VISION_NUM_CTX,
    "synthesis": OLLAMA_SYNTHESIS_NUM_CTX,
    "coordinator": OLLAMA_SYNTHESIS_NUM_CTX,
    "code": OLLAMA_CODE_NUM_CTX,
    "code_agent": OLLAMA_CODE_NUM_CTX,
}
_DISCOVERED_CONTEXTS: dict[str, int] = {}
_UNKNOWN_MODEL_NUM_CTX = 4096


def declared_context_for(model: str) -> int | None:
    if model in _DISCOVERED_CONTEXTS:
        return _DISCOVERED_CONTEXTS[model]
    value = OLLAMA_MODELS.get(model, {}).get("context_length")
    return int(value) if value else None


def resolve_context_budget(
    model: str, role: str | None = None, *, declared_max: int | None = None,
    requested: int | None = None,
) -> int:
    """Resolve an explicit local runtime window and never exceed model max."""
    budget = requested if requested is not None else _ROLE_BUDGETS.get(
        role or "", OLLAMA_DEFAULT_NUM_CTX
    )
    maximum = declared_max if declared_max is not None else declared_context_for(model)
    if maximum:
        return max(1, min(int(budget), maximum))
    # An unregistered model has no verified architectural limit before the
    # first successful /api/show. Keep every role, including code and custom
    # vision, at Ollama's conservative baseline until discovery proves more.
    if model not in OLLAMA_MODELS:
        return min(max(1, int(budget)), _UNKNOWN_MODEL_NUM_CTX)
    return max(1, int(budget))


@dataclass(frozen=True)
class ModelInventory:
    """Snapshot of which configured models are actually usable right now.

    - ``configured``: every model id in the registry (``OLLAMA_MODELS``).
    - ``installed``: registry ids present in Ollama ``/api/tags``.
    - ``healthy``: installed models we treat as usable (installed == healthy for
      now; a cheap probe could narrow this later).
    - ``tools_capable``: healthy models whose registry entry sets ``tools``.
    - ``vision_capable``: the configured vision model, only if installed.
    - ``discovery_ok``: False when ``/api/tags`` failed or returned nothing — in
      which case the sets above are empty (fail closed) rather than assumed.
    """

    configured: frozenset[str] = field(default_factory=frozenset)
    installed: frozenset[str] = field(default_factory=frozenset)
    healthy: frozenset[str] = field(default_factory=frozenset)
    tools_capable: frozenset[str] = field(default_factory=frozenset)
    vision_capable: frozenset[str] = field(default_factory=frozenset)
    # EVERY model name Ollama reports, registry or not. Role assignments from
    # the settings page may deliberately use any installed model; the healthy/
    # tools sets above stay registry-scoped for the automatic picker.
    installed_all: frozenset[str] = field(default_factory=frozenset)
    capabilities: dict[str, ModelCapabilities] = field(default_factory=dict)
    discovery_ok: bool = False

    def is_healthy(self, model: str | None) -> bool:
        return bool(model) and model in self.healthy

    def safe_default_model(self) -> str:
        """A model that is safe to route to.

        Prefers the primary ``OLLAMA_MODEL`` when verified healthy; otherwise
        returns it anyway as the known-safe fallback (callers record a reason).
        When discovery failed we cannot verify anything, so the primary model is
        the single least-surprising choice.
        """
        if self.is_healthy(OLLAMA_MODEL):
            return OLLAMA_MODEL
        return OLLAMA_MODEL


async def _fetch_installed_names() -> set[str] | None:
    """Return the set of model names from Ollama ``/api/tags``.

    Returns ``None`` on any failure (the caller fails closed). A successful call
    that lists zero models also yields ``None`` — an empty Ollama is not a
    licence to assume the registry is installed.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{model_settings.ollama_base_url()}/api/tags")
            resp.raise_for_status()
            models = resp.json().get("models", [])
    except Exception as exc:  # noqa: BLE001 — any failure is fail-closed
        global _DISCOVERED_CONTEXTS
        _DISCOVERED_CONTEXTS = {}
        logger.warning("model discovery failed (Ollama /api/tags): %s", exc)
        return None
    names = {m["name"] for m in models if isinstance(m, dict) and m.get("name")}
    if not names:
        _DISCOVERED_CONTEXTS = {}
        logger.warning("model discovery returned no installed models; failing closed")
        return None
    return names


async def get_model_inventory() -> ModelInventory:
    """Query Ollama and return a fail-closed :class:`ModelInventory`."""
    configured = frozenset(OLLAMA_MODELS)
    installed_names = await _fetch_installed_names()
    if installed_names is None:
        # Fail closed: advertise nothing we cannot verify.
        return ModelInventory(configured=configured, discovery_ok=False)
    discovered = await discover_model_capabilities(installed_names)
    return build_inventory(installed_names, discovered)


async def discover_model_capabilities(model_names: set[str]) -> dict[str, dict]:
    """Best-effort enrichment from Ollama ``/api/show``.

    Tags proves installation, while show is the authoritative source for model
    metadata.  A show failure does not invalidate the installation snapshot;
    registry metadata remains an explicit fallback for known stock models.
    """
    result: dict[str, dict] = {}
    semaphore = asyncio.Semaphore(4)

    async def inspect(name: str) -> tuple[str, dict] | None:
        async with semaphore:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{model_settings.ollama_base_url()}/api/show", json={"model": name}
                    )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:  # noqa: BLE001 - per-model fallback
                logger.warning("capability discovery failed for %s: %s", name, exc)
                return None
            model_info = data.get("model_info") or {}
            context_values = [
                int(value) for key, value in model_info.items()
                if str(key).endswith(".context_length")
                and isinstance(value, (int, float)) and value > 0
            ]
            item = {
                "declared_context": max(context_values) if context_values else None,
            }
            if isinstance(data.get("capabilities"), list):
                caps = set(data["capabilities"])
                item.update({
                    "tools": "tools" in caps,
                    "thinking": "thinking" in caps,
                    "vision": "vision" in caps,
                    "embedding": "embedding" in caps,
                })
            return name, item

    current_contexts: dict[str, int] = {}
    for found in await asyncio.gather(*(inspect(name) for name in model_names)):
        if found is not None:
            name, item = found
            result[name] = item
            if item.get("declared_context"):
                current_contexts[name] = int(item["declared_context"])
    # Replace the authority snapshot as one binding. Removed models, failed show
    # probes, and responses without context metadata cannot retain stale limits.
    global _DISCOVERED_CONTEXTS
    _DISCOVERED_CONTEXTS = current_contexts
    return result


def build_inventory(
    installed_names: set[str], discovered: dict[str, dict] | None = None,
) -> ModelInventory:
    """Build an inventory from an already-fetched set of installed model names.

    Split out so tests (and any synchronous caller that already has the names)
    can construct an inventory without HTTP. Only configured models can be
    healthy/tools/vision capable — an installed model with no registry entry is
    not something Pilot routes to.
    """
    configured = frozenset(OLLAMA_MODELS)
    installed = frozenset(name for name in installed_names if name in configured)
    healthy = installed  # installed == healthy for now
    tools_capable = frozenset(
        mid for mid in healthy if OLLAMA_MODELS.get(mid, {}).get("tools")
    )
    vision_capable = (
        frozenset({OLLAMA_VISION_MODEL})
        if OLLAMA_VISION_MODEL in healthy
        else frozenset()
    )
    discovered = discovered or {}
    capabilities = {}
    for mid in installed_names:
        entry = OLLAMA_MODELS.get(mid, {})
        live = discovered.get(mid, {})
        declared = live.get("declared_context") or declared_context_for(mid)
        role = "vision" if mid == OLLAMA_VISION_MODEL else None
        capabilities[mid] = ModelCapabilities(
            declared_context=declared,
            effective_context=resolve_context_budget(mid, role, declared_max=declared),
            effective_contexts={
                role_name: resolve_context_budget(
                    mid, role_name, declared_max=declared
                )
                for role_name in ("default", "classifier", "gateway", "vision", "synthesis", "code_agent")
            },
            tools=bool(live.get("tools", entry.get("tools"))),
            thinking=bool(live.get("thinking", entry.get("thinking"))),
            vision=bool(live.get("vision", entry.get("vision")) or mid == OLLAMA_VISION_MODEL),
            embedding=bool(live.get("embedding", entry.get("embedding"))),
        )
    tools_capable = frozenset(
        mid for mid in healthy if capabilities.get(mid) and capabilities[mid].tools
    )
    vision_capable = frozenset(
        mid for mid in healthy if capabilities.get(mid) and capabilities[mid].vision
    )
    return ModelInventory(
        configured=configured,
        installed=installed,
        healthy=healthy,
        tools_capable=tools_capable,
        vision_capable=vision_capable,
        installed_all=frozenset(installed_names),
        capabilities=capabilities,
        discovery_ok=True,
    )
