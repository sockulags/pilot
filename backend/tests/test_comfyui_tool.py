import os
import sys
import tempfile
import unittest
from pathlib import Path

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

        client = httpx.Client(transport=httpx.MockTransport(handler))

        result = comfyui.generate_image(
            "red robot",
            base_url="http://127.0.0.1:8188",
            comfyui_dir=r"C:\Users\dev\Code\ComfyUI",
            checkpoint="model.safetensors",
            client=client,
            poll_interval=0,
        )

        self.assertIn("ComfyUI is not reachable", result)
        self.assertIn("http://127.0.0.1:8188", result)

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
            comfyui_dir=r"C:\Users\dev\Code\ComfyUI",
            checkpoint="model.safetensors",
            output_dir=r"C:\Users\dev\Code\ComfyUI\output",
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


if __name__ == "__main__":
    unittest.main()
