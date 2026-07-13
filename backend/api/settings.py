"""REST API for model settings — providers, roles and live model discovery.

Mounted on the main backend app (same origin as the frontend). Guarded by the
same shared secret as the WebSocket (`PILOT_AUTH_TOKEN`): when a token is
configured, requests must present it as `Authorization: Bearer <token>` or an
`X-Pilot-Token` header. API keys never leave the backend — GET responses mask
them (see model_settings.masked_settings) and PUT merges stored secrets back in.
"""

from __future__ import annotations

import secrets

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

import model_settings
from agents.model_inventory import build_inventory, discover_model_capabilities
from agents import local_runtime
from config import ANSWER_BACKEND, OLLAMA_MODEL, OLLAMA_MODELS, PILOT_AUTH_TOKEN
from config import AGENT_ROLE_MODELS


def _request_token(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    token = request.headers.get("X-Pilot-Token")
    return token.strip() if token else None


def _auth_ok(request: Request) -> bool:
    if not PILOT_AUTH_TOKEN:
        return True
    presented = _request_token(request) or ""
    return secrets.compare_digest(presented, PILOT_AUTH_TOKEN)


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def _role_defaults() -> dict:
    """The env-default model per role (what an unassigned role falls back to)."""
    defaults = dict(AGENT_ROLE_MODELS)
    defaults.update(model_settings.PIPELINE_ROLE_ENV_DEFAULTS)
    return defaults


def _settings_payload() -> dict:
    return {
        "settings": model_settings.masked_settings(),
        "role_catalog": model_settings.ROLE_CATALOG,
        "role_env_defaults": _role_defaults(),
        "env": {
            "default_model": OLLAMA_MODEL,
            "answer_backend": ANSWER_BACKEND,
        },
    }


async def _fetch_ollama_models(base_url: str) -> tuple[bool, list[dict], str]:
    """(ok, models, detail) from an Ollama /api/tags call — ALL installed models."""
    try:
        runtime = model_settings.local_runtime_snapshot()
        if runtime.kind != "ollama" or runtime.base_url != base_url.rstrip("/"):
            runtime = local_runtime.LocalRuntimeConfig(kind="ollama", base_url=base_url)
        base_url = local_runtime.validate_local_endpoint(runtime)
        async with local_runtime.client(10) as client:
            if runtime.effective_key:
                resp = await client.get(
                    f"{base_url.rstrip('/')}/api/tags",
                    headers=local_runtime.runtime_headers(runtime),
                )
            else:
                resp = await client.get(f"{base_url.rstrip('/')}/api/tags")
            resp.raise_for_status()
            raw = resp.json().get("models", [])
    except Exception as exc:  # noqa: BLE001 — connectivity result, not a crash
        return False, [], f"{type(exc).__name__}: {exc}"
    models = []
    names = {
        str(m["name"]) for m in raw
        if isinstance(m, dict) and m.get("name")
    }
    inventory = build_inventory(names, await discover_model_capabilities(names))
    for m in raw:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        name = str(m["name"])
        registry = OLLAMA_MODELS.get(name)
        caps = inventory.capabilities.get(name)
        models.append({
            "id": name,
            "size": m.get("size"),
            "label": registry["label"] if registry else name,
            "hint": registry["hint"] if registry else "",
            "tools": registry.get("tools", True) if registry else True,
            "in_registry": registry is not None,
            "declared_context": caps.declared_context if caps else None,
            "effective_context": caps.effective_context if caps else None,
            "effective_contexts": caps.effective_contexts if caps else {},
            "capabilities": {
                "tools": caps.tools,
                "thinking": caps.thinking,
                "vision": caps.vision,
                "embedding": caps.embedding,
            } if caps else {},
        })
    return True, models, f"{len(models)} modeller installerade"


async def _test_openai_provider(base_url: str, api_key: str) -> tuple[bool, str]:
    if not api_key:
        return False, "API-nyckel saknas"
    try:
        base_url = local_runtime.validate_cloud_endpoint(base_url)
        async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
            resp = await client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if resp.status_code == 401:
                return False, "401 — ogiltig API-nyckel"
            resp.raise_for_status()
            count = len(resp.json().get("data", []))
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"OK — {count} modeller tillgängliga"


def create_settings_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/settings/models")
    async def get_settings(request: Request):
        if not _auth_ok(request):
            return _unauthorized()
        return _settings_payload()

    @router.put("/settings/models")
    async def put_settings(body: dict, request: Request):
        if not _auth_ok(request):
            return _unauthorized()
        merged = model_settings.apply_client_update(body or {})
        _saved, errors = model_settings.save_settings(merged)
        payload = _settings_payload()
        payload["errors"] = errors
        status = 200 if not errors else 400
        return JSONResponse(payload, status_code=status)

    @router.get("/models/available")
    async def available_models(request: Request):
        if not _auth_ok(request):
            return _unauthorized()
        settings = model_settings.load_settings()
        runtime = model_settings.local_runtime_snapshot(settings)
        if runtime.kind == "ollama":
            ok, models, detail = await _fetch_ollama_models(runtime.base_url)
        else:
            try:
                names = await local_runtime.discover(runtime)
                ok = True
                models = [{
                    "id": name, "label": name, "hint": "OpenAI-compatible local runtime",
                    "tools": runtime.capabilities.tools == "supported", "in_registry": False,
                    "declared_context": runtime.context_overrides.get(name),
                    "effective_context": min(runtime.context_overrides.get(name, 4096), 4096),
                    "effective_contexts": {}, "capabilities": runtime.capabilities.__dict__,
                } for name in names]
                detail = f"{len(models)} modeller installerade"
            except local_runtime.LocalRuntimeError as exc:
                ok, models, detail = False, [], f"{exc.code}: {exc}"
        cloud = []
        for entry in settings.get("cloud_providers", []):
            cloud.append({
                "id": entry.get("id"),
                "label": entry.get("label"),
                "enabled": bool(entry.get("enabled", True)),
                "has_key": bool(model_settings.provider_api_key(entry)),
                "models": list(entry.get("models") or []),
            })
        return {
            "ollama": {"ok": ok, "base_url": runtime.base_url, "detail": detail, "models": models},
            "local_runtime": {
                "ok": ok, "kind": runtime.kind, "base_url": runtime.base_url,
                "detail": detail, "models": models, "fingerprint": runtime.fingerprint,
            },
            "cloud": cloud,
        }

    @router.post("/settings/test-provider")
    async def test_provider(body: dict, request: Request):
        if not _auth_ok(request):
            return _unauthorized()
        body = body or {}
        provider_id = str(body.get("provider") or "").strip().lower()
        if provider_id in {"ollama", "local"}:
            stored = model_settings.local_runtime_snapshot()
            requested_kind = str(body.get("kind") or stored.kind)
            if requested_kind not in {"ollama", "openai_compatible"}:
                return {"ok": False, "detail": "invalid runtime kind", "models": []}
            candidate = local_runtime.LocalRuntimeConfig(
                kind=requested_kind,  # type: ignore[arg-type]
                base_url=str(body.get("base_url") or stored.base_url),
                api_key=str(body.get("api_key") or stored.api_key),
                api_key_env=stored.api_key_env,
                allow_private_network=bool(body.get("allow_private_network", stored.allow_private_network)),
                chat_model=stored.chat_model, vision_model=stored.vision_model,
                embedding_model=stored.embedding_model, context_overrides=stored.context_overrides,
                capabilities=stored.capabilities,
            )
            try:
                names = await local_runtime.discover(candidate)
                return {"ok": True, "detail": f"OK — {len(names)} modeller tillgängliga", "models": names}
            except local_runtime.LocalRuntimeError as exc:
                return {"ok": False, "detail": f"{exc.code}: {exc}", "models": []}
        # Cloud: test inline values when provided (unsaved form state), else the
        # stored provider entry. Inline api_key lets the user test before saving.
        settings = model_settings.load_settings()
        stored = next(
            (e for e in settings.get("cloud_providers", []) if e.get("id") == provider_id),
            None,
        )
        base_url = str(body.get("base_url") or (stored or {}).get("base_url") or "")
        api_key = str(body.get("api_key") or "")
        if not api_key and stored:
            api_key = model_settings.provider_api_key(stored)
        if not base_url:
            return {"ok": False, "detail": "base_url saknas"}
        ok, detail = await _test_openai_provider(base_url, api_key)
        return {"ok": ok, "detail": detail}

    return router
