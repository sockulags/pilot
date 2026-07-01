# ComfyUI Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a first-pass `generate_image` Pilot tool that calls a running local ComfyUI server and returns the generated image path.

**Architecture:** Implement a focused ComfyUI client in `backend/tools/comfyui.py`, expose it through the existing tool registry, and dispatch it from `agents/loop.py`. Step 1 does not auto-start ComfyUI and does not add inline image preview; existing result artifacts display the returned path and metadata.

**Tech Stack:** Python 3.12, `httpx`, unittest, Pilot tool registry/coordinator, ComfyUI HTTP API.

---

## File Structure

- Create `backend/tools/comfyui.py`: ComfyUI configuration, checkpoint discovery, workflow construction, HTTP prompt submission, history polling, and text result rendering.
- Modify `backend/config.py`: add `COMFYUI_BASE_URL`, `COMFYUI_DIR`, `COMFYUI_CHECKPOINT`, `COMFYUI_OUTPUT_DIR`, and timeout defaults.
- Modify `backend/tools/registry.py`: add `generate_image` to coordinator tools and capability manifest.
- Modify `backend/agents/loop.py`: import and dispatch `generate_image`.
- Modify `backend/agents/orchestrator.py`: remove the “no built-in image generation tool” limitation once the registry includes the real tool.
- Modify `README.md`: document ComfyUI configuration and usage.
- Test `backend/tests/test_comfyui_tool.py`: unit tests for client behavior with mocked HTTP.
- Modify `backend/tests/test_registry.py`: registry and capability assertions.
- Modify `backend/tests/test_agent_safety.py`: loop dispatch assertion.
- Modify `backend/tests/test_ws_policy.py`: update image-generation capability prompt assertion.

---

### Task 1: ComfyUI Client Tests And Implementation

**Files:**
- Create: `backend/tests/test_comfyui_tool.py`
- Create: `backend/tools/comfyui.py`
- Modify: `backend/config.py`

- [ ] **Step 1: Write failing tests for checkpoint discovery and missing server**

Create `backend/tests/test_comfyui_tool.py`:

```python
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ComfyUIToolTests(unittest.TestCase):
    def test_discover_checkpoint_picks_first_supported_file(self):
        from tools import comfyui

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            checkpoints = root / "models" / "checkpoints"
            checkpoints.mkdir(parents=True)
            (checkpoints / "zeta.safetensors").write_text("", encoding="utf-8")
            (checkpoints / "alpha.ckpt").write_text("", encoding="utf-8")

            selected = comfyui.discover_checkpoint(str(root), "")

        self.assertEqual("alpha.ckpt", selected)

    def test_generate_image_reports_unreachable_comfyui(self):
        from tools import comfyui

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        transport = httpx.MockTransport(handler)
        client = httpx.Client(transport=transport)

        result = comfyui.generate_image(
            "red robot",
            base_url="http://127.0.0.1:8188",
            comfyui_dir=r"C:\Users\lucas\Code\ComfyUI",
            checkpoint="model.safetensors",
            client=client,
            poll_interval=0,
        )

        self.assertIn("ComfyUI is not reachable", result)
        self.assertIn("http://127.0.0.1:8188", result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_comfyui_tool
```

Expected: fails because `tools.comfyui` does not exist.

- [ ] **Step 3: Add ComfyUI config**

In `backend/config.py`, after the vision config block, add:

```python
# --- ComfyUI image generation ----------------------------------------------
COMFYUI_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")
COMFYUI_DIR = os.getenv("COMFYUI_DIR", r"C:\Users\lucas\Code\ComfyUI")
COMFYUI_CHECKPOINT = os.getenv("COMFYUI_CHECKPOINT", "")
COMFYUI_OUTPUT_DIR = os.getenv(
    "COMFYUI_OUTPUT_DIR",
    os.path.join(COMFYUI_DIR, "output"),
)
COMFYUI_TIMEOUT_SECONDS = float(os.getenv("COMFYUI_TIMEOUT_SECONDS", "180"))
```

- [ ] **Step 4: Implement minimal ComfyUI client**

Create `backend/tools/comfyui.py`:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_comfyui_tool
```

Expected: `Ran 2 tests` and `OK`.

---

### Task 2: Mocked Prompt/History Success Path

**Files:**
- Modify: `backend/tests/test_comfyui_tool.py`
- Modify: `backend/tools/comfyui.py`

- [ ] **Step 1: Add failing test for mocked ComfyUI generation**

Append to `ComfyUIToolTests`:

```python
    def test_generate_image_returns_output_path_from_history(self):
        from tools import comfyui

        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(f"{request.method} {request.url.path}")
            if request.url.path == "/system_stats":
                return httpx.Response(200, json={"system": {}})
            if request.url.path == "/prompt":
                return httpx.Response(200, json={"prompt_id": "abc123"})
            if request.url.path == "/history/abc123":
                return httpx.Response(200, json={
                    "abc123": {
                        "outputs": {
                            "9": {
                                "images": [
                                    {"filename": "pilot_00001_.png", "subfolder": "", "type": "output"}
                                ]
                            }
                        }
                    }
                })
            return httpx.Response(404)

        client = httpx.Client(transport=httpx.MockTransport(handler))

        result = comfyui.generate_image(
            "red robot",
            width=512,
            height=512,
            steps=12,
            seed=42,
            base_url="http://127.0.0.1:8188",
            comfyui_dir=r"C:\Users\lucas\Code\ComfyUI",
            checkpoint="model.safetensors",
            output_dir=r"C:\Users\lucas\Code\ComfyUI\output",
            client=client,
            poll_interval=0,
        )

        self.assertIn("Generated image with ComfyUI", result)
        self.assertIn("pilot_00001_.png", result)
        self.assertIn("Seed: 42", result)
        self.assertEqual([
            "GET /system_stats",
            "POST /prompt",
            "GET /history/abc123",
        ], calls)
```

- [ ] **Step 2: Run the success-path test**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_comfyui_tool.ComfyUIToolTests.test_generate_image_returns_output_path_from_history
```

Expected: PASS if Task 1 implementation already supports prompt/history.

- [ ] **Step 3: Fix only if the test fails**

If the test fails, update `extract_output_images` and output-path rendering in `backend/tools/comfyui.py` so the result includes `Path(output_dir) / filename` for each image returned under `history["outputs"][...]["images"]`.

- [ ] **Step 4: Run all ComfyUI tool tests**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_comfyui_tool
```

Expected: all tests pass.

---

### Task 3: Registry Exposure

**Files:**
- Modify: `backend/tools/registry.py`
- Modify: `backend/tests/test_registry.py`

- [ ] **Step 1: Add failing registry tests**

In `backend/tests/test_registry.py`, update `test_coordinator_allowlist` expected set to include `"generate_image"`.

Add to `test_capability_manifest_lists_real_tools_grouped`:

```python
        self.assertIn("generate_image", manifest)
        self.assertIn("Image generation", manifest)
```

Add to `test_tool_schemas_are_function_shaped`:

```python
        generate_image = next(s for s in schemas if s["function"]["name"] == "generate_image")
        props = generate_image["function"]["parameters"]["properties"]
        self.assertEqual({"prompt", "width", "height", "steps", "seed"}, set(props))
        self.assertEqual(["prompt"], generate_image["function"]["parameters"]["required"])
```

- [ ] **Step 2: Run registry tests to verify failure**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_registry.RegistryDerivationTests.test_coordinator_allowlist tests.test_registry.RegistryDerivationTests.test_capability_manifest_lists_real_tools_grouped tests.test_registry.RegistryDerivationTests.test_tool_schemas_are_function_shaped
```

Expected: fails because `generate_image` is not registered.

- [ ] **Step 3: Add registry category label and tool spec**

In `backend/tools/registry.py`, add to `_CATEGORY_LABELS`:

```python
    "image": "Image generation",
```

Add this `ToolSpec` before the code-agent section:

```python
    ToolSpec(
        name="generate_image",
        summary="Generate an image with local ComfyUI",
        description="generate_image(prompt, width?, height?, steps?, seed?): create an image using the local ComfyUI server",
        when_to_use="When the user asks to generate, create, draw, or make a new image from text. Do not use for screenshot interpretation.",
        params={
            "prompt": {"type": "string", "description": "Image prompt to generate"},
            "width": {"type": "integer", "description": "Image width in pixels (optional, default 1024)"},
            "height": {"type": "integer", "description": "Image height in pixels (optional, default 1024)"},
            "steps": {"type": "integer", "description": "Sampling steps (optional, default 25)"},
            "seed": {"type": "integer", "description": "Seed (optional; random when omitted)"},
        },
        required=("prompt",),
        category="image",
        deterministic=True,
        risk_level="medium",
        side_effects=True,
    ),
```

- [ ] **Step 4: Run registry tests to verify pass**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_registry
```

Expected: registry tests pass.

---

### Task 4: Loop Dispatch

**Files:**
- Modify: `backend/agents/loop.py`
- Modify: `backend/tests/test_agent_safety.py`

- [ ] **Step 1: Add failing loop dispatch test**

In `backend/tests/test_agent_safety.py`, add to `AgentLoopTests`:

```python
    def test_execute_tool_dispatches_generate_image(self):
        asyncio.run(self._execute_tool_dispatches_generate_image())
```

Add the helper method in `AgentLoopTests`:

```python
    async def _execute_tool_dispatches_generate_image(self):
        from agents import loop

        with mock.patch.object(loop, "generate_image", return_value="Generated image with ComfyUI\nFiles:\nC:\\out\\pilot.png") as generate:
            result = await loop.execute_tool(
                "generate_image",
                {"prompt": "red robot", "width": 512, "height": 512, "steps": 12, "seed": 42},
                lambda e: None,
            )

        self.assertIn("C:\\out\\pilot.png", result)
        generate.assert_called_once_with("red robot", width=512, height=512, steps=12, seed=42)
```

- [ ] **Step 2: Run loop dispatch test to verify failure**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_agent_safety.AgentLoopTests.test_execute_tool_dispatches_generate_image
```

Expected: fails because `loop.generate_image` is not imported or dispatch does not handle the tool.

- [ ] **Step 3: Import and dispatch `generate_image`**

In `backend/agents/loop.py`, add import:

```python
from tools.comfyui import generate_image
```

In `_execute_tool_text`, before the external MCP branch, add:

```python
    elif tool == "generate_image":
        return await asyncio.to_thread(
            generate_image,
            args["prompt"],
            width=args.get("width", 1024),
            height=args.get("height", 1024),
            steps=args.get("steps", 25),
            seed=args.get("seed"),
        )
```

- [ ] **Step 4: Run loop dispatch test to verify pass**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_agent_safety.AgentLoopTests.test_execute_tool_dispatches_generate_image
```

Expected: PASS.

---

### Task 5: Capability Wording And Documentation

**Files:**
- Modify: `backend/agents/orchestrator.py`
- Modify: `backend/tests/test_ws_policy.py`
- Modify: `README.md`

- [ ] **Step 1: Update failing prompt test**

In `backend/tests/test_ws_policy.py`, replace `test_chat_prompt_states_image_generation_limit_without_denying_tools` with:

```python
    def test_chat_prompt_lists_image_generation_capability(self):
        from agents.orchestrator import _build_reply_messages

        messages = _build_reply_messages(
            [{"role": "user", "content": "Kan du generera bilder?"}],
            outcome=None,
        )
        system = messages[0]["content"]

        self.assertIn("generate_image", system)
        self.assertIn("Image generation", system)
        self.assertNotIn("not have a built-in image generation tool", system)
```

- [ ] **Step 2: Run prompt test to verify failure**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_ws_policy.WebSocketPolicyTests.test_chat_prompt_lists_image_generation_capability
```

Expected: fails until `orchestrator.py` no longer says image generation is unavailable and registry includes the tool.

- [ ] **Step 3: Remove outdated limitation from chat system prompt**

In `backend/agents/orchestrator.py`, remove this sentence block:

```python
    "Pilot does not have a built-in image generation tool in the current tool "
    "set; if asked about image generation, say that limitation directly while "
    "still describing the computer, web, file, and coding tools it does have. "
```

- [ ] **Step 4: Add README section**

In `README.md`, under Requirements, add:

```markdown
- Optional for image generation: [ComfyUI](https://github.com/comfyanonymous/ComfyUI) running locally on `http://127.0.0.1:8188` with at least one checkpoint under `models/checkpoints`.
```

Under Environment variables, add:

```markdown
| `COMFYUI_BASE_URL` | `http://127.0.0.1:8188` | Local ComfyUI API URL used by `generate_image` |
| `COMFYUI_DIR` | `C:\Users\lucas\Code\ComfyUI` | ComfyUI installation folder |
| `COMFYUI_CHECKPOINT` | *(first checkpoint found)* | Checkpoint filename to use for image generation |
| `COMFYUI_OUTPUT_DIR` | `<COMFYUI_DIR>\output` | Folder where generated images are reported |
```

- [ ] **Step 5: Run prompt/doc-adjacent tests**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_ws_policy
```

Expected: all tests pass.

---

### Task 6: Final Verification And Manual Smoke Readiness

**Files:**
- No new code files.

- [ ] **Step 1: Run focused backend tests**

Run:

```powershell
cd C:\Users\lucas\Code\pilot\backend
uv run python -m unittest tests.test_comfyui_tool tests.test_registry tests.test_agent_safety tests.test_ws_policy
```

Expected: all tests pass.

- [ ] **Step 2: Check ComfyUI runtime status**

Run:

```powershell
try { Invoke-RestMethod http://127.0.0.1:8188/system_stats | Out-Null; "ComfyUI running" } catch { "ComfyUI not running" }
```

Expected right now: likely `ComfyUI not running`.

- [ ] **Step 3: Check checkpoint availability**

Run:

```powershell
Get-ChildItem C:\Users\lucas\Code\ComfyUI\models\checkpoints -File | Where-Object { $_.Extension -in ".safetensors",".ckpt",".pt",".pth" } | Select-Object -First 5 Name
```

Expected right now: no real checkpoint files were found during planning. Manual image generation will need a checkpoint before smoke testing can pass.

- [ ] **Step 4: Report status**

Final status should include:

- Tests run and pass/fail counts.
- Whether ComfyUI is running.
- Whether a checkpoint is present.
- Exact restart instruction: restart Pilot backend so the new registry/tool is loaded.
