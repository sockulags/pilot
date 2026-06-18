import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class ComposeReplyFallbackTests(unittest.TestCase):
    def test_stream_chat_payload_disables_thinking(self):
        from agents import orchestrator

        payload = orchestrator._chat_payload(
            [{"role": "user", "content": "Hej"}],
            "gemma4:12b",
            stream=True,
        )

        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], True)
        self.assertEqual("gemma4:12b", payload["model"])

    def test_empty_model_reply_falls_back_to_action_log(self):
        asyncio.run(self._empty_model_reply_falls_back_to_action_log())

    async def _empty_model_reply_falls_back_to_action_log(self):
        from agents import orchestrator
        from agents.loop import LoopOutcome

        async def empty_stream(messages, model=None):
            if False:
                yield ""

        outcome = LoopOutcome(
            "max_steps",
            "- read_file({'path': 'backend/api/ws.py'}) -> websocket_endpoint handles messages",
            "",
        )

        with mock.patch.object(orchestrator, "_stream_ollama_chat", new=empty_stream):
            parts = [
                part
                async for part in orchestrator.compose_reply(
                    [{"role": "user", "content": "Förklara backendflödet"}],
                    outcome,
                    "gpt-oss:20b",
                )
            ]

        reply = "".join(parts)
        self.assertIn("read_file", reply)
        self.assertNotEqual("Klar", reply)


if __name__ == "__main__":
    unittest.main()
