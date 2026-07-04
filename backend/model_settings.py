"""Persisted model settings — providers and per-role model assignments.

This is the runtime-editable layer above the env config: which model providers
exist (local Ollama plus any number of OpenAI-compatible cloud endpoints) and
which provider+model each *role* runs on. The design contract:

- **Default runs everything.** With no settings file (or no assignment for a
  role) behaviour is exactly today's env-driven behaviour: `OLLAMA_MODEL` on the
  `PILOT_ANSWER_BACKEND` backend, with the per-role env defaults in
  `config.AGENT_ROLE_MODELS` as the next fallback. The settings layer only ever
  *overrides*, never replaces, that chain — deleting the file restores stock
  behaviour.
- **Local and cloud mix per role.** A role assignment names a provider id and a
  model, so research can run on a cloud model while code stays on local
  devstral. Cloud-assigned models are encoded as ``cloud:<provider>:<model>``
  ids so they flow through the same code paths as Ollama ids (the provider
  layer routes on the prefix).
- **Fail closed / degrade gracefully.** A role assigned to a disabled or
  missing provider resolves to nothing and callers fall back to the default
  chain with a recorded reason — a bad settings file must never take turns down.

Storage: one JSON file (`data/model_settings.json`, gitignored like the session
and memory stores), atomic writes. API keys are stored in this local file —
Pilot is a single-user local app; the file lives outside git and the API layer
never returns keys to the browser (see :func:`masked_settings`).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import tempfile
import threading

from config import (
    OLLAMA_BASE_URL,
    OLLAMA_GATEWAY_MODEL,
    OLLAMA_MODEL,
    OLLAMA_ROUTER_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
)

logger = logging.getLogger(__name__)

MODEL_SETTINGS_FILE = os.getenv(
    "MODEL_SETTINGS_FILE",
    os.path.join(os.path.dirname(__file__), "data", "model_settings.json"),
)

# Cloud-assigned models travel through the agent as one opaque id so every
# existing code path (decision loop, consult menu, eval meta) handles them
# unchanged. Ollama ids contain single colons (gemma4:12b), so the prefix and
# a fixed 3-part split keep the two namespaces unambiguous.
CLOUD_PREFIX = "cloud:"

_PROVIDER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")

# Every role Pilot can assign a model to. `agent` roles select the coordinator
# model per turn intent (see turn_policy.select_agent_for_intent); `pipeline`
# roles are fixed stages of every turn. vision/embeddings stay local by design
# (they handle raw screen content and the memory store) — the UI shows them,
# but only Ollama models may be assigned.
ROLE_CATALOG: list[dict] = [
    {"id": "default_agent", "label": "Standard", "kind": "agent",
     "description": "Kör allt som inte har en egen tilldelning — beslutsloop, chatt och fallback."},
    {"id": "research_agent", "label": "Research", "kind": "agent",
     "description": "Webbresearch och sammanvägning av källor."},
    {"id": "code_agent", "label": "Kod", "kind": "agent",
     "description": "Agentiskt repoarbete och längre koduppgifter."},
    {"id": "quick_code_agent", "label": "Snabb kod", "kind": "agent",
     "description": "Snabba kod- och teknikfrågor."},
    {"id": "deep_reasoning_agent", "label": "Djupt resonemang", "kind": "agent",
     "description": "Matematik och klurig analys."},
    {"id": "vision_agent", "label": "Vision", "kind": "agent", "local_only": True,
     "description": "Bildförståelse av skärmen. Körs alltid lokalt."},
    {"id": "classifier", "label": "Klassificerare", "kind": "pipeline",
     "description": "Väljer rutt för varje tur. Bör vara snabb."},
    {"id": "gateway", "label": "Gateway", "kind": "pipeline",
     "description": "Förfinar/översätter instruktioner före expertkonsultation."},
    {"id": "synthesis", "label": "Svarssyntes", "kind": "pipeline",
     "description": "Skriver det slutliga svaret utifrån insamlat underlag."},
]

ROLE_IDS = {role["id"] for role in ROLE_CATALOG}
LOCAL_ONLY_ROLES = {role["id"] for role in ROLE_CATALOG if role.get("local_only")}

# Fallback model per pipeline role when no assignment exists (agent roles fall
# back through config.AGENT_ROLE_MODELS in turn_policy instead).
PIPELINE_ROLE_ENV_DEFAULTS = {
    "classifier": OLLAMA_ROUTER_MODEL,
    "gateway": OLLAMA_GATEWAY_MODEL,
    "synthesis": OLLAMA_MODEL,
}

_lock = threading.Lock()
_cache: dict | None = None
_cache_mtime: float | None = None


# --------------------------------------------------------------------------- #
# Cloud model id encoding
# --------------------------------------------------------------------------- #


def cloud_model_id(provider_id: str, model: str) -> str:
    return f"{CLOUD_PREFIX}{provider_id}:{model}"


def is_cloud_model_id(model: str | None) -> bool:
    return bool(model) and str(model).startswith(CLOUD_PREFIX)


def parse_cloud_model_id(model: str) -> tuple[str, str] | None:
    """Split ``cloud:<provider>:<model>`` -> (provider_id, model). None if not cloud."""
    if not is_cloud_model_id(model):
        return None
    rest = model[len(CLOUD_PREFIX):]
    provider_id, sep, model_name = rest.partition(":")
    if not sep or not provider_id or not model_name:
        return None
    return provider_id, model_name


# --------------------------------------------------------------------------- #
# Defaults / load / save
# --------------------------------------------------------------------------- #


def _default_settings() -> dict:
    """Stock settings — mirrors current env behaviour so first run changes nothing.

    When OPENAI_API_KEY is configured in the env we surface it as a ready-made
    cloud provider so the settings page starts populated; its key is read from
    the env at call time (marked ``api_key_env``) rather than copied into the
    settings file.
    """
    cloud: list[dict] = []
    if OPENAI_API_KEY:
        cloud.append({
            "id": "openai",
            "label": "OpenAI",
            "base_url": OPENAI_BASE_URL,
            "api_key": "",
            "api_key_env": "OPENAI_API_KEY",
            "models": [OPENAI_MODEL],
            "enabled": True,
        })
    return {
        "version": 1,
        "ollama": {"base_url": OLLAMA_BASE_URL},
        "cloud_providers": cloud,
        "roles": {},
    }


def load_settings() -> dict:
    """Load settings (cached; reloaded when the file changes on disk)."""
    global _cache, _cache_mtime
    with _lock:
        try:
            mtime = os.path.getmtime(MODEL_SETTINGS_FILE)
        except OSError:
            mtime = None
        if _cache is not None and mtime == _cache_mtime:
            return copy.deepcopy(_cache)
        if mtime is None:
            _cache, _cache_mtime = _default_settings(), None
            return copy.deepcopy(_cache)
        try:
            with open(MODEL_SETTINGS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            settings, errors = validate_settings(raw)
            if errors:
                logger.warning(
                    "model settings file has issues (%s); using the valid subset",
                    "; ".join(errors),
                )
            _cache, _cache_mtime = settings, mtime
        except Exception as exc:  # noqa: BLE001 — a corrupt file must not kill turns
            logger.warning("could not load model settings (%s); using defaults", exc)
            _cache, _cache_mtime = _default_settings(), mtime
        return copy.deepcopy(_cache)


def save_settings(raw: dict) -> tuple[dict, list[str]]:
    """Validate and persist settings. Returns (saved_settings, errors).

    On validation errors nothing is written and the current settings are
    returned unchanged alongside the error list.
    """
    global _cache, _cache_mtime
    settings, errors = validate_settings(raw)
    if errors:
        return load_settings(), errors
    directory = os.path.dirname(MODEL_SETTINGS_FILE)
    with _lock:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            os.replace(tmp, MODEL_SETTINGS_FILE)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        _cache = copy.deepcopy(settings)
        try:
            _cache_mtime = os.path.getmtime(MODEL_SETTINGS_FILE)
        except OSError:
            _cache_mtime = None
    return copy.deepcopy(settings), []


def reset_cache_for_tests() -> None:
    global _cache, _cache_mtime
    with _lock:
        _cache, _cache_mtime = None, None


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def validate_settings(raw: dict) -> tuple[dict, list[str]]:
    """Coerce ``raw`` into a valid settings dict, collecting human-readable errors.

    Invalid parts are dropped (with an error recorded) rather than failing the
    whole document, so one bad role assignment cannot brick the settings page.
    """
    errors: list[str] = []
    if not isinstance(raw, dict):
        return _default_settings(), ["settings must be a JSON object"]

    out = _default_settings()

    ollama = raw.get("ollama")
    if isinstance(ollama, dict):
        base_url = str(ollama.get("base_url") or "").strip().rstrip("/")
        if base_url and _valid_url(base_url):
            out["ollama"] = {"base_url": base_url}
        elif base_url:
            errors.append(f"ogiltig Ollama-URL: {base_url!r}")

    seen_ids: set[str] = set()
    providers: list[dict] = []
    for entry in raw.get("cloud_providers") or []:
        if not isinstance(entry, dict):
            errors.append("cloud provider entry is not an object")
            continue
        pid = str(entry.get("id") or "").strip().lower()
        if not _PROVIDER_ID.match(pid):
            errors.append(f"ogiltigt leverantörs-id: {pid!r}")
            continue
        if pid == "ollama" or pid in seen_ids:
            errors.append(f"leverantörs-id måste vara unikt (inte 'ollama'): {pid!r}")
            continue
        base_url = str(entry.get("base_url") or "").strip().rstrip("/")
        if not _valid_url(base_url):
            errors.append(f"ogiltig base_url för {pid}: {base_url!r}")
            continue
        models = [
            str(m).strip() for m in (entry.get("models") or []) if str(m).strip()
        ]
        api_key_env = str(entry.get("api_key_env") or "").strip()
        provider = {
            "id": pid,
            "label": str(entry.get("label") or pid).strip()[:64] or pid,
            "base_url": base_url,
            "api_key": str(entry.get("api_key") or ""),
            "models": models[:50],
            "enabled": bool(entry.get("enabled", True)),
        }
        if api_key_env:
            provider["api_key_env"] = api_key_env
        seen_ids.add(pid)
        providers.append(provider)
    out["cloud_providers"] = providers

    roles: dict[str, dict] = {}
    raw_roles = raw.get("roles")
    if isinstance(raw_roles, dict):
        for role_id, assignment in raw_roles.items():
            if role_id not in ROLE_IDS:
                errors.append(f"okänd roll: {role_id!r}")
                continue
            if assignment is None:
                continue  # null = inherit; simply omit
            if not isinstance(assignment, dict):
                errors.append(f"ogiltig tilldelning för {role_id}")
                continue
            provider = str(assignment.get("provider") or "").strip().lower()
            model = str(assignment.get("model") or "").strip()
            if not provider or not model:
                errors.append(f"tilldelning för {role_id} saknar provider/modell")
                continue
            if provider != "ollama" and provider not in seen_ids:
                errors.append(
                    f"rollen {role_id} pekar på okänd leverantör {provider!r}"
                )
                continue
            if role_id in LOCAL_ONLY_ROLES and provider != "ollama":
                errors.append(f"rollen {role_id} kan bara köras lokalt (Ollama)")
                continue
            roles[role_id] = {"provider": provider, "model": model}
    out["roles"] = roles
    return out, errors


def _valid_url(url: str) -> bool:
    return bool(re.match(r"^https?://[^\s]+$", url or ""))


# --------------------------------------------------------------------------- #
# Resolution — the read API used by the provider layer and turn policy
# --------------------------------------------------------------------------- #


def ollama_base_url() -> str:
    """Effective Ollama URL: the settings value, falling back to the env."""
    settings = load_settings()
    return str(settings.get("ollama", {}).get("base_url") or OLLAMA_BASE_URL)


def cloud_provider(provider_id: str, settings: dict | None = None) -> dict | None:
    """Return an *enabled and usable* cloud provider entry, or None."""
    settings = settings or load_settings()
    for entry in settings.get("cloud_providers", []):
        if entry.get("id") == provider_id:
            if not entry.get("enabled", True):
                return None
            if not provider_api_key(entry):
                return None
            return dict(entry)
    return None


def provider_api_key(entry: dict) -> str:
    """A provider's effective API key: stored value, else its named env var."""
    key = str(entry.get("api_key") or "")
    if key:
        return key
    env_name = str(entry.get("api_key_env") or "")
    if env_name:
        return os.getenv(env_name, "")
    return ""


def settings_persisted() -> bool:
    """True when the user has actually saved settings (the file exists).

    Distinguishes deliberate configuration from the env-seeded defaults: an
    OPENAI_API_KEY sitting in .env pre-populates the settings *page*, but it must
    not silently change data flow (e.g. advertising cloud experts) until the
    user has saved the settings once — Pilot is local-first by default.
    """
    return os.path.exists(MODEL_SETTINGS_FILE)


def enabled_cloud_providers(settings: dict | None = None) -> list[dict]:
    """Cloud providers usable for routing — only from *persisted* settings."""
    if settings is None:
        if not settings_persisted():
            return []
        settings = load_settings()
    return [
        dict(entry)
        for entry in settings.get("cloud_providers", [])
        if entry.get("enabled", True) and provider_api_key(entry)
    ]


def resolve_role_model(role: str) -> str | None:
    """The model id a role is explicitly assigned to, or None to inherit.

    Ollama assignments return the bare model id; cloud assignments return the
    ``cloud:<provider>:<model>`` id. An assignment pointing at a missing or
    disabled provider resolves to None (fail closed to the default chain).
    """
    settings = load_settings()
    assignment = settings.get("roles", {}).get(role)
    if not assignment:
        return None
    provider = assignment.get("provider")
    model = assignment.get("model")
    if not provider or not model:
        return None
    if provider == "ollama":
        return str(model)
    if cloud_provider(provider, settings) is None:
        logger.warning(
            "role %r is assigned to unavailable provider %r; inheriting default",
            role, provider,
        )
        return None
    return cloud_model_id(provider, str(model))


def default_role_model(role: str) -> str | None:
    """Fallback model for a pipeline role when it has no assignment."""
    return PIPELINE_ROLE_ENV_DEFAULTS.get(role)


def resolve_pipeline_model(role: str) -> str:
    """Effective model for a pipeline role: assignment > default_agent > env."""
    assigned = resolve_role_model(role)
    if assigned:
        return assigned
    default = resolve_role_model("default_agent")
    if default:
        return default
    return default_role_model(role) or OLLAMA_MODEL


# --------------------------------------------------------------------------- #
# API-facing views
# --------------------------------------------------------------------------- #

_KEEP_KEY = "__KEEP__"


def masked_settings(settings: dict | None = None) -> dict:
    """Settings safe to send to the browser: API keys replaced by presence info."""
    settings = settings or load_settings()
    masked = copy.deepcopy(settings)
    for entry in masked.get("cloud_providers", []):
        key = provider_api_key(entry)
        entry["api_key"] = ""
        entry["has_key"] = bool(key)
        entry["key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else ""
    return masked


def apply_client_update(raw: dict) -> dict:
    """Merge a browser PUT payload with stored secrets.

    The browser never sees stored API keys, so an unchanged provider arrives
    with an empty or ``__KEEP__`` api_key — restore the stored key for any
    provider id that already exists. A non-empty new key replaces the old one.
    """
    current = load_settings()
    existing_keys = {
        entry.get("id"): entry.get("api_key", "")
        for entry in current.get("cloud_providers", [])
    }
    existing_env = {
        entry.get("id"): entry.get("api_key_env", "")
        for entry in current.get("cloud_providers", [])
    }
    merged = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    for entry in merged.get("cloud_providers") or []:
        if not isinstance(entry, dict):
            continue
        pid = str(entry.get("id") or "").strip().lower()
        key = str(entry.get("api_key") or "")
        if (not key or key == _KEEP_KEY) and pid in existing_keys:
            entry["api_key"] = existing_keys[pid]
            if existing_env.get(pid) and not entry.get("api_key_env"):
                entry["api_key_env"] = existing_env[pid]
        entry.pop("has_key", None)
        entry.pop("key_hint", None)
    return merged
