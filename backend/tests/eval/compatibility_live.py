"""Opt-in live compatibility evidence for Pilot local runtimes.

This module is deliberately absent from pytest/CI.  It uses the production
local-runtime boundary, makes no support inference from discovery alone, and
writes immutable JSON + Markdown to an explicit caller-selected path.

Run from ``backend``::

    uv run python -m tests.eval.compatibility_live --preset all \
        --output ../artifacts/pilot-local-compat-2026-07-13

Existing output files are refused unless ``--overwrite`` is explicitly passed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import hashlib
import io
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import httpx  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

import model_settings  # noqa: E402
from agents import local_runtime, providers  # noqa: E402
from agents.context_manager import estimate_message_tokens  # noqa: E402
from tests.eval.compatibility import (  # noqa: E402
    REPORT_SCHEMA_VERSION,
    comparison_key,
    scenario_ids,
)


PRESETS = {
    "ollama": {
        "name": "Ollama native API", "kind": "ollama",
        "base_url": "http://127.0.0.1:11434", "endpoint_class": "loopback",
    },
    "ollama-openai": {
        "name": "Ollama OpenAI-compatible API", "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:11434/v1", "endpoint_class": "loopback",
    },
    "lm-studio": {
        "name": "LM Studio OpenAI-compatible API", "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:1234/v1", "endpoint_class": "loopback",
    },
    "llama-cpp": {
        "name": "llama.cpp OpenAI-compatible API", "kind": "openai_compatible",
        "base_url": "http://127.0.0.1:8080/v1", "endpoint_class": "loopback",
    },
}


def _run(argv: list[str]) -> str | None:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=5, check=False,
            cwd=Path(__file__).resolve().parents[3],
        )
        return (result.stdout or result.stderr).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _ram_bytes() -> int | None:
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        return int(status.total_phys) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
    try:
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        return None


def _gpu() -> dict[str, Any]:
    query = "name,memory.total,driver_version"
    value = _run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"])
    if not value:
        return {"detected": False, "name": None, "vram_mb": None, "driver": None}
    first = value.splitlines()[0].split(",")
    if len(first) < 3:
        return {"detected": True, "name": value[:160], "vram_mb": None, "driver": None}
    try:
        vram = int(first[1].strip())
    except ValueError:
        vram = None
    return {"detected": True, "name": first[0].strip(), "vram_mb": vram, "driver": first[2].strip()}


def _environment() -> dict[str, Any]:
    sha = _run(["git", "rev-parse", "HEAD"])
    dirty = bool(_run(["git", "status", "--porcelain"]))
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_sha": sha, "git_dirty": dirty, "source_digest": _source_digest(),
        "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "python": platform.python_version(),
        "hardware": {
            "cpu": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER") or "unknown",
            "logical_cpu_count": os.cpu_count(), "ram_bytes": _ram_bytes(), "gpu": _gpu(),
        },
    }


def _digest_source_state(
    git_sha: bytes, diff: bytes, untracked: list[tuple[str, bytes]],
) -> str:
    """Pure, collision-delimited digest input for tests and the live runner."""
    digest = hashlib.sha256()
    for label, payload in ((b"git-sha", git_sha), (b"tracked-diff", diff)):
        digest.update(label + b"\0" + str(len(payload)).encode() + b"\0" + payload)
    for relative, payload in sorted(untracked):
        encoded = relative.replace("\\", "/").encode()
        digest.update(b"untracked\0" + str(len(encoded)).encode() + b"\0" + encoded)
        digest.update(str(len(payload)).encode() + b"\0" + payload)
    return digest.hexdigest()


def _source_digest() -> str:
    """Identify tracked diff + untracked source without recording user paths.

    Generated compatibility reports are excluded: they are outputs, not runner
    source, and including a prior report would make identical code hash itself.
    """
    root = Path(__file__).resolve().parents[3]
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            timeout=10, check=False,
        ).stdout.strip()
        diff = subprocess.run(
            ["git", "diff", "--binary", "HEAD", "--", ".",
             ":!backend/tests/eval/results/compatibility/*"],
            cwd=root, capture_output=True, timeout=10, check=False,
        )
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"],
            cwd=root, capture_output=True, timeout=10, check=False,
        ).stdout.split(b"\0")
        source_files: list[tuple[str, bytes]] = []
        for raw in sorted(item for item in untracked if item):
            relative = raw.decode("utf-8", errors="replace").replace("\\", "/")
            if relative.startswith("backend/tests/eval/results/compatibility/"):
                continue
            path = root / relative
            if path.is_file():
                source_files.append((relative, path.read_bytes()))
        return _digest_source_state(git_sha, diff.stdout, source_files)
    except (OSError, subprocess.SubprocessError):
        return _digest_source_state(b"unavailable", b"", [])


def _safe_detail(exc: BaseException) -> str:
    """Return bounded diagnostic classification without attacker-controlled text."""
    if isinstance(exc, local_runtime.LocalRuntimeError):
        return f"LocalRuntimeError:{exc.code}"
    if isinstance(exc, httpx.HTTPError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        suffix = f":status={status}" if isinstance(status, int) else ""
        return f"httpx:{type(exc).__name__}{suffix}"
    return type(exc).__name__


def _config(preset: str, model: str, embedding_model: str, context: int) -> local_runtime.LocalRuntimeConfig:
    item = PRESETS[preset]
    caps = local_runtime.RuntimeCapabilities(
        tools="supported" if preset in {"ollama", "ollama-openai"} else "unknown",
        vision="supported" if preset == "ollama" else "unknown",
        embeddings="supported" if preset in {"ollama", "ollama-openai"} else "unknown",
        structured_output="unknown",
    )
    return local_runtime.LocalRuntimeConfig(
        kind=item["kind"], base_url=item["base_url"], chat_model=model,
        vision_model=model if preset == "ollama" else "", embedding_model=embedding_model,
        context_overrides={model: context}, capabilities=caps,
    )


async def _metadata(config: local_runtime.LocalRuntimeConfig, model: str) -> dict[str, Any]:
    base = local_runtime.validate_local_endpoint(config)
    models = await local_runtime.discover(config)
    resolved_model = model if model in models else next(
        (installed for installed in models if installed == f"{model}:latest"), None
    )
    if resolved_model is None:
        raise local_runtime.LocalRuntimeError("model_missing", f"Required model {model!r} is not installed")
    result: dict[str, Any] = {
        "id": resolved_model, "requested_id": model, "digest": None, "quantization": None,
        "declared_context": None,
    }
    # Native Ollama and Ollama's /v1 compatibility facade share the same model
    # store. Query native metadata only when /api/version proves the underlying
    # server is Ollama; never infer this for LM Studio or llama.cpp.
    native_base = base if config.kind == "ollama" else base.removesuffix("/v1")
    is_ollama = config.kind == "ollama"
    if not is_ollama:
        try:
            async with local_runtime.client(5) as client:
                version = await client.get(
                    native_base + "/api/version", headers=local_runtime.runtime_headers(config),
                )
                version.raise_for_status()
                is_ollama = bool(version.json().get("version"))
        except Exception:  # noqa: BLE001 - generic runtimes need not expose this
            is_ollama = False
    if is_ollama:
        async with local_runtime.client(15) as client:
            tags = await client.get(native_base + "/api/tags", headers=local_runtime.runtime_headers(config))
            tags.raise_for_status()
            for row in tags.json().get("models") or []:
                if row.get("name") == resolved_model:
                    result["digest"] = row.get("digest")
                    result["quantization"] = (row.get("details") or {}).get("quantization_level")
                    break
            show = await client.post(
                native_base + "/api/show", json={"model": resolved_model},
                headers=local_runtime.runtime_headers(config),
            )
            show.raise_for_status()
            info = show.json()
            model_info = info.get("model_info") or {}
            for key, value in model_info.items():
                if str(key).endswith(".context_length") and isinstance(value, int):
                    result["declared_context"] = value
                    break
    return result


async def _runtime_version(config: local_runtime.LocalRuntimeConfig) -> str | None:
    base = local_runtime.validate_local_endpoint(config)
    # Ollama exposes /api/version outside /v1; the generic preset intentionally
    # records the underlying server version without claiming LM Studio semantics.
    url = base.removesuffix("/v1") + "/api/version"
    try:
        async with local_runtime.client(5) as client:
            response = await client.get(url, headers=local_runtime.runtime_headers(config))
            response.raise_for_status()
            return str(response.json().get("version") or "") or None
    except Exception:  # noqa: BLE001 - version is optional metadata
        return None


def _image_message() -> tuple[list[dict], int]:
    image = Image.new("RGB", (1920, 1080), "#12365c")
    draw = ImageDraw.Draw(image)
    draw.rectangle((140, 140, 1780, 940), fill="#e9c46a")
    draw.text((800, 510), "PILOT", fill="#17202a")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    message = {
        "role": "user",
        "content": (
            "Inspect this generated 1920x1080 full-screen image. State the word in the centre "
            "and the two dominant colours. Be concise. " + "Preserve screen evidence. " * 120
        ),
        "images": [encoded], "context_kind": "active_task",
    }
    estimate = estimate_message_tokens(message)[0]
    return [message], estimate


def _weather_tool_is_grounded(value: dict) -> bool:
    for call in value.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != "get_weather":
            continue
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return False
        return isinstance(arguments, dict) and arguments.get("city") == "Stockholm"
    return False


def _vision_is_grounded(value: dict) -> bool:
    text = str(value.get("content") or "").lower()
    return "pilot" in text and any(color in text for color in ("yellow", "gold")) and any(
        color in text for color in ("blue", "navy")
    )


def _scenario_result(
    scenario_id: str, verdict: str, started: float, *, detail: str = "",
    usage: dict | None = None, reports: list | None = None,
) -> dict[str, Any]:
    report = (reports or [])[-1] if reports else None
    budget = None
    if report:
        budget = {
            key: getattr(report, key) for key in (
                "context_window", "completion_reserve", "prompt_budget",
                "estimated_prompt_tokens", "media_tokens", "tool_schema_tokens",
                "compacted", "retry",
            )
        }
    return {
        "id": scenario_id, "verdict": verdict,
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        "budget": budget, "token_usage": usage or {}, "detail": detail[:500],
    }


async def _measure(
    scenario_id: str, operation: Callable[[], Awaitable[Any]], check: Callable[[Any], bool],
    describe: Callable[[Any], dict[str, Any]] | None = None,
):
    providers.reset_usage()
    started = time.perf_counter()
    try:
        value = await operation()
        verdict = "supported" if check(value) else "failed"
        detail = "contract satisfied" if verdict == "supported" else "response did not satisfy deterministic contract"
    except Exception as exc:  # noqa: BLE001 - evidence must record failures without fabricating passes
        verdict, detail = "failed", _safe_detail(exc)
    row = _scenario_result(
        scenario_id, verdict, started, detail=detail,
        usage=providers.get_usage(), reports=providers.get_context_reports(),
    )
    if verdict == "supported" and describe is not None:
        row.update(describe(value))
    return row


async def _run_available(preset: str, config: local_runtime.LocalRuntimeConfig, model: str, embedding_model: str) -> list[dict]:
    scenarios: list[dict] = []
    original_snapshot = model_settings.local_runtime_snapshot
    model_settings.local_runtime_snapshot = lambda: config
    try:
        if config.kind == "ollama":
            async def once(messages, tools=None):
                return await providers._ollama_once(messages, model, tools, 0)
        else:
            async def once(messages, tools=None):
                return await providers._local_openai_once(config, messages, model, tools, 0)

        scenarios.append(await _measure(
            "text", lambda: once([{"role": "user", "content": "Reply with exactly PILOT_COMPAT_OK"}]),
            lambda value: "PILOT_COMPAT_OK" in value.get("content", ""),
        ))
        tool = [{"type": "function", "function": {
            "name": "get_weather", "description": "Get weather for a city",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        }}]
        scenarios.append(await _measure(
            "native_tool",
            lambda: once([{"role": "user", "content": "Call get_weather for Stockholm now. Do not answer in prose."}], tool),
            _weather_tool_is_grounded,
        ))
        if preset == "ollama":
            messages, raw_estimate = _image_message()
            row = await _measure(
                "vision_long_8192", lambda: once(messages),
                _vision_is_grounded,
                lambda _value: {"result": {
                    "expected_word_observed": True,
                    "expected_color_families_observed": True,
                }},
            )
            row["input"] = {"image_resolution": "1920x1080", "estimated_prompt_tokens": raw_estimate}
            if raw_estimate <= 4096:
                row.update(verdict="failed", detail="fixture estimate did not exceed 4096")
            scenarios.append(row)
        if preset in {"ollama", "ollama-openai"}:
            scenarios.append(await _measure(
                "embeddings", lambda: local_runtime.embed(config, embedding_model, ["search_document: Pilot compatibility"]),
                lambda value: bool(value and value[0]),
                lambda value: {
                    "input": {"model": embedding_model},
                    "result": {"vector_dimension": len(value[0])},
                },
            ))
    finally:
        model_settings.local_runtime_snapshot = original_snapshot
    return scenarios


def _profile_verdict(scenarios: list[dict], availability: str) -> str:
    if availability != "available":
        return "unverified"
    verdicts = [row.get("verdict") for row in scenarios]
    supported = verdicts.count("supported")
    if "failed" in verdicts:
        return "failed"
    if supported and any(verdict != "supported" for verdict in verdicts):
        return "limited"
    if supported and supported == len(verdicts):
        return "supported"
    if "unsupported" in verdicts:
        return "unsupported"
    return "unverified"


def _unverified_scenarios(detail: str) -> list[dict]:
    return [
        {"id": scenario, "verdict": "unverified", "latency_ms": None,
         "budget": None, "token_usage": {}, "detail": detail}
        for scenario in scenario_ids()
    ]


async def _profile(
    preset: str, model: str, embedding_model: str, context: int,
    metadata_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    item = PRESETS[preset]
    config = _config(preset, model, embedding_model, context)
    base = {
        "id": preset, "runtime": {
            "kind": config.kind, "name": item["name"], "version": None,
            "endpoint_class": item["endpoint_class"], "fingerprint": config.fingerprint,
        },
        "model": {"id": model, "digest": None, "quantization": None, "declared_context": None},
        "embedding_model": None,
        "effective_context": context,
        "capabilities": asdict(config.capabilities),
        "availability": "unverified", "verdict": "unverified", "scenarios": [],
        "metadata_provenance": {},
    }
    try:
        base["model"] = await _metadata(config, model)
        if preset in {"ollama", "ollama-openai"}:
            base["embedding_model"] = await _metadata(config, embedding_model)
        base["runtime"]["version"] = await _runtime_version(config)
    except Exception as exc:  # noqa: BLE001
        base["availability_detail"] = _safe_detail(exc)
        base["scenarios"] = _unverified_scenarios("runtime/model unavailable")
        return base
    base["availability"] = "available"

    if preset in {"lm-studio", "llama-cpp"}:
        supplied = metadata_override or {}
        mapping = {
            "runtime_version": (base["runtime"], "version"),
            "model_digest": (base["model"], "digest"),
            "quantization": (base["model"], "quantization"),
            "declared_context": (base["model"], "declared_context"),
        }
        for key, (target, field) in mapping.items():
            if supplied.get(key) not in (None, ""):
                target[field] = supplied[key]
                base["metadata_provenance"][field] = "user_supplied_cli"
        missing = [
            key for key, (target, field) in mapping.items()
            if target.get(field) in (None, "")
        ]
        if missing:
            base["metadata_status"] = "missing"
            base["metadata_missing"] = missing
            base["scenarios"] = _unverified_scenarios(
                "exact runtime/model metadata required before live support measurement"
            )
            base["verdict"] = "unverified"
            return base
        base["metadata_status"] = "complete"

    measured = await _run_available(preset, config, model, embedding_model)
    measured_ids = {row["id"] for row in measured}
    measured.extend({
        "id": scenario, "verdict": "unverified", "latency_ms": None,
        "budget": None, "token_usage": {},
        "detail": "deterministic contract only; not issued to the live runtime",
    } for scenario in scenario_ids() if scenario not in measured_ids)
    order = {scenario: index for index, scenario in enumerate(scenario_ids())}
    base["scenarios"] = sorted(measured, key=lambda row: order[row["id"]])
    base["verdict"] = _profile_verdict(base["scenarios"], base["availability"])
    return base


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Pilot local inference compatibility evidence", "",
        f"- Schema: `{report['schema_version']}`",
        f"- Timestamp (UTC): `{report['run']['timestamp_utc']}`",
        f"- Git: `{report['run']['git_sha']}` (dirty: `{str(report['run']['git_dirty']).lower()}`)",
        f"- Source digest: `{report['run']['source_digest']}`",
        f"- Comparison key: `{report['comparison_key']}`", "",
        "| Runtime profile | Version | Chat/vision model | Digest | Quant | Embedding model | Context | Availability | Verdict |",
        "|---|---:|---|---|---|---|---:|---|---|",
    ]
    for profile in report["profiles"]:
        runtime, model = profile["runtime"], profile["model"]
        embedding = profile.get("embedding_model") or {}
        embedding_text = (
            f"{embedding.get('id')} / {embedding.get('digest') or 'unknown'} / "
            f"{embedding.get('quantization') or 'unknown'}"
            if embedding else "not tested"
        )
        lines.append(
            f"| {runtime['name']} | {runtime['version'] or 'unknown'} | {model['id']} | "
            f"{model['digest'] or 'unknown'} | {model['quantization'] or 'unknown'} | "
            f"{embedding_text} | {profile['effective_context']} | {profile['availability']} | "
            f"{profile.get('verdict', 'unverified')} |"
        )
    lines.extend(["", "## Scenario verdicts", "", "| Profile | Scenario | Verdict | Estimated prompt | Latency ms | Detail |", "|---|---|---|---:|---:|---|"])
    for profile in report["profiles"]:
        for scenario in profile["scenarios"]:
            budget = scenario.get("budget") or {}
            lines.append(
                f"| {profile['id']} | {scenario['id']} | {scenario['verdict']} | "
                f"{budget.get('estimated_prompt_tokens', 'n/a')} | {scenario.get('latency_ms') or 'n/a'} | "
                f"{str(scenario.get('detail') or '').replace('|', '/')} |"
            )
    lines.extend([
        "", "`unverified` means no successful live request was made. It is not a support claim.",
        "Secrets, endpoint URLs, environment values, and user paths are intentionally absent.", "",
    ])
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preset", choices=["all", *PRESETS], default="all")
    parser.add_argument("--output", type=Path, required=True, help="Output stem; writes .json and .md")
    parser.add_argument("--model", default="qwen3.5:9b")
    parser.add_argument("--embedding-model", default="nomic-embed-text")
    parser.add_argument("--context", type=int, default=8192)
    parser.add_argument("--runtime-version")
    parser.add_argument("--model-digest")
    parser.add_argument("--quantization")
    parser.add_argument("--declared-context", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.context < 8192:
        parser.error("--context must be at least 8192 for the realistic vision scenario")
    metadata_override = {
        "runtime_version": args.runtime_version,
        "model_digest": args.model_digest,
        "quantization": args.quantization,
        "declared_context": args.declared_context,
    }
    if any(value not in (None, "") for value in metadata_override.values()) and args.preset not in {
        "lm-studio", "llama-cpp",
    }:
        parser.error("metadata overrides require a single lm-studio or llama-cpp preset")
    if args.declared_context is not None and args.declared_context < 256:
        parser.error("--declared-context must be at least 256")
    json_path, md_path = args.output.with_suffix(".json"), args.output.with_suffix(".md")
    if not args.overwrite and (json_path.exists() or md_path.exists()):
        parser.error("output exists; choose a new output stem or pass --overwrite")
    presets = list(PRESETS) if args.preset == "all" else [args.preset]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION, "run": _environment(),
        "profiles": [
            await _profile(
                name, args.model, args.embedding_model, args.context,
                metadata_override if name == args.preset else None,
            ) for name in presets
        ],
    }
    report["comparison_key"] = comparison_key(report)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(json_path)
    print(md_path)
    required = {
        "ollama": {"text", "native_tool", "vision_long_8192", "embeddings"},
        "ollama-openai": {"text", "native_tool", "embeddings"},
    }
    if args.preset == "all":
        checked = [profile for profile in report["profiles"] if profile["id"] in required]
    else:
        checked = report["profiles"]
        required.setdefault(args.preset, {"text"})
    ok = bool(checked)
    for profile in checked:
        verdicts = {row["id"]: row["verdict"] for row in profile["scenarios"]}
        ok = ok and profile["availability"] == "available" and all(
            verdicts.get(scenario) == "supported" for scenario in required[profile["id"]]
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
