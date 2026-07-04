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
from dataclasses import dataclass, field

import httpx

import model_settings
from config import (
    OLLAMA_MODEL,
    OLLAMA_MODELS,
    OLLAMA_VISION_MODEL,
)

logger = logging.getLogger(__name__)


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
        logger.warning("model discovery failed (Ollama /api/tags): %s", exc)
        return None
    names = {m["name"] for m in models if isinstance(m, dict) and m.get("name")}
    if not names:
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
    return build_inventory(installed_names)


def build_inventory(installed_names: set[str]) -> ModelInventory:
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
    return ModelInventory(
        configured=configured,
        installed=installed,
        healthy=healthy,
        tools_capable=tools_capable,
        vision_capable=vision_capable,
        installed_all=frozenset(installed_names),
        discovery_ok=True,
    )
