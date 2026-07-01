from __future__ import annotations

import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from config import (
    COMFYUI_BASE_URL,
    COMFYUI_CHECKPOINT,
    COMFYUI_DIR,
    COMFYUI_OUTPUT_DIR,
    COMFYUI_TIMEOUT_SECONDS,
)

CHECKPOINT_EXTENSIONS = (".safetensors", ".ckpt", ".pt", ".pth")


def discover_checkpoint(comfyui_dir: str = COMFYUI_DIR, configured: str = COMFYUI_CHECKPOINT) -> str:
    if configured.strip():
        return configured.strip()
    checkpoint_dir = Path(comfyui_dir) / "models" / "checkpoints"
    if not checkpoint_dir.exists():
        return ""
    candidates = sorted(
        path.name
        for path in checkpoint_dir.iterdir()
        if path.is_file() and path.suffix.lower() in CHECKPOINT_EXTENSIONS
    )
    return candidates[0] if candidates else ""


def generate_image(
    prompt: str,
    width: int = 1024,
    height: int = 1024,
    steps: int = 25,
    seed: int | None = None,
    *,
    base_url: str = COMFYUI_BASE_URL,
    comfyui_dir: str = COMFYUI_DIR,
    checkpoint: str = COMFYUI_CHECKPOINT,
    output_dir: str = COMFYUI_OUTPUT_DIR,
    timeout_seconds: float = COMFYUI_TIMEOUT_SECONDS,
    poll_interval: float = 1.0,
    client: httpx.Client | None = None,
) -> str:
    clean_prompt = str(prompt or "").strip()
    if not clean_prompt:
        return "generate_image requires a non-empty prompt."

    selected_checkpoint = discover_checkpoint(comfyui_dir, checkpoint)
    if not selected_checkpoint:
        looked = Path(comfyui_dir) / "models" / "checkpoints"
        return (
            "No ComfyUI checkpoint found. "
            f"Looked in {looked}. Set COMFYUI_CHECKPOINT or add a checkpoint file there."
        )

    actual_seed = int(seed if seed is not None else random.randint(0, 2**32 - 1))
    workflow = build_text_to_image_workflow(
        clean_prompt,
        selected_checkpoint,
        width=int(width),
        height=int(height),
        steps=int(steps),
        seed=actual_seed,
    )
    owns_client = client is None
    http = client or httpx.Client(timeout=10)
    try:
        try:
            http.get(f"{base_url.rstrip('/')}/system_stats")
            response = http.post(
                f"{base_url.rstrip('/')}/prompt",
                json={"prompt": workflow, "client_id": str(uuid.uuid4())},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            return f"ComfyUI is not reachable at {base_url}: {exc}"

        prompt_id = response.json().get("prompt_id")
        if not prompt_id:
            return f"ComfyUI did not return a prompt_id. Response: {response.text[:500]}"

        history = wait_for_history(http, base_url, str(prompt_id), timeout_seconds, poll_interval)
        images = extract_output_images(history)
        if not images:
            return f"ComfyUI finished prompt {prompt_id}, but no output images were found."

        output_paths = [str(Path(output_dir) / image["filename"]) for image in images]
        return (
            "Generated image with ComfyUI\n"
            f"Prompt: {clean_prompt}\n"
            f"Checkpoint: {selected_checkpoint}\n"
            f"Size: {int(width)}x{int(height)}\n"
            f"Steps: {int(steps)}\n"
            f"Seed: {actual_seed}\n"
            "Files:\n"
            + "\n".join(output_paths)
        )
    except TimeoutError as exc:
        return str(exc)
    finally:
        if owns_client:
            http.close()


def build_text_to_image_workflow(
    prompt: str,
    checkpoint: str,
    *,
    width: int,
    height: int,
    steps: int,
    seed: int,
) -> dict[str, Any]:
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": 7.0,
                "sampler_name": "euler",
                "scheduler": "normal",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "low quality, blurry", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "pilot", "images": ["8", 0]}},
    }


def wait_for_history(
    client: httpx.Client,
    base_url: str,
    prompt_id: str,
    timeout_seconds: float,
    poll_interval: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    url = f"{base_url.rstrip('/')}/history/{prompt_id}"
    while time.monotonic() < deadline:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        if prompt_id in payload:
            return payload[prompt_id]
        time.sleep(poll_interval)
    raise TimeoutError(f"ComfyUI prompt {prompt_id} did not finish within {timeout_seconds:g} seconds.")


def extract_output_images(history: dict[str, Any]) -> list[dict[str, str]]:
    images: list[dict[str, str]] = []
    for node in history.get("outputs", {}).values():
        for image in node.get("images", []) or []:
            filename = str(image.get("filename") or "").strip()
            if filename:
                images.append({"filename": filename})
    return images
