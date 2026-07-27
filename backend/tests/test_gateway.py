"""Language gateway: direct tests for ``refine_query``.

Every *caller* test replaces ``refine_query`` via ``mock.patch.object``, so the
real body was never exercised. These tests call the real async function and stub
only the provider boundary (``agents.providers.chat_once``), mirroring the
direct-provider style in ``test_provider_roles.py``.
"""

import asyncio
import unittest
from unittest import mock

from agents import gateway, providers


def _refine(conversation, task, model=None):
    return asyncio.run(gateway.refine_query(conversation, task, model))


class _ChatOnceStub:
    """Record calls and return a fixed content (or raise a fixed error)."""

    def __init__(self, *, content=None, raises=None):
        self.calls: list[tuple[tuple, dict]] = []
        self._content = content
        self._raises = raises

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._raises is not None:
            raise self._raises
        return {"content": self._content}


class RefineQueryDisabledTests(unittest.TestCase):
    def test_disabled_flag_returns_verbatim_and_skips_provider(self):
        stub = _ChatOnceStub(content="should not be used")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", False), \
                mock.patch.object(providers, "chat_once", stub):
            result = _refine(None, "  Fixa buggen i main.py  ")
        # Task is still stripped, but returned verbatim otherwise.
        self.assertEqual(result, "Fixa buggen i main.py")
        self.assertEqual(stub.calls, [])

    def test_empty_task_short_circuits_regardless_of_flag(self):
        stub = _ChatOnceStub(content="nope")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            self.assertEqual(_refine(None, "   \n\t  "), "")
            self.assertEqual(_refine(None, None), "")
        self.assertEqual(stub.calls, [])


class RefineQueryNormalPathTests(unittest.TestCase):
    def test_normal_refine_returns_stripped_result_with_gateway_role(self):
        stub = _ChatOnceStub(content="  Fix the bug in main.py  ")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            result = _refine(None, "Fixa buggen i main.py")

        self.assertEqual(result, "Fix the bug in main.py")
        self.assertEqual(len(stub.calls), 1)
        args, kwargs = stub.calls[0]
        self.assertEqual(kwargs.get("role"), "gateway")
        # messages is the first positional argument.
        messages = args[0]
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], gateway._REFINE_SYSTEM)
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("Fixa buggen i main.py", messages[1]["content"])

    def test_caller_model_is_forwarded_to_provider(self):
        stub = _ChatOnceStub(content="refined")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            _refine(None, "do a thing", model="custom-model")
        args, _ = stub.calls[0]
        # model is the second positional argument.
        self.assertEqual(args[1], "custom-model")


class RefineQueryConversationContextTests(unittest.TestCase):
    def test_only_last_four_messages_included_and_truncated_to_300(self):
        oldest = "OLDEST_SENTINEL_should_not_appear"
        long_content = "x" * 500  # over the 300-char cap
        conversation = [
            {"role": "user", "content": oldest},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
            {"role": "assistant", "content": long_content},
            {"role": "user", "content": "fifth-most-recent"},
        ]
        stub = _ChatOnceStub(content="refined")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            _refine(conversation, "the task")

        args, _ = stub.calls[0]
        user_content = args[0][1]["content"]
        # The 5th-from-last message must be dropped (only last 4 kept).
        self.assertNotIn(oldest, user_content)
        self.assertIn("second", user_content)
        self.assertIn("fifth-most-recent", user_content)
        # The over-length message is truncated to exactly 300 chars.
        self.assertIn("x" * 300, user_content)
        self.assertNotIn("x" * 301, user_content)


class RefineQueryFailOpenTests(unittest.TestCase):
    def test_provider_error_returns_verbatim_task(self):
        stub = _ChatOnceStub(raises=RuntimeError("provider down"))
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            result = _refine(None, "keep me verbatim")
        self.assertEqual(result, "keep me verbatim")
        self.assertEqual(len(stub.calls), 1)


class RefineQueryGuardTests(unittest.TestCase):
    def test_empty_rewrite_falls_back_to_original(self):
        stub = _ChatOnceStub(content="   \n  ")
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            self.assertEqual(_refine(None, "original task"), "original task")

    def test_missing_content_key_falls_back_to_original(self):
        # chat_once returning no content at all must also fail open.
        async def no_content(*args, **kwargs):
            return {}

        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", no_content):
            self.assertEqual(_refine(None, "original task"), "original task")

    def test_runaway_rewrite_at_limit_is_accepted(self):
        task = "hi"  # limit = max(400, 2*6) = 400
        refined = "a" * 400
        stub = _ChatOnceStub(content=refined)
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            self.assertEqual(_refine(None, task), refined)

    def test_runaway_rewrite_one_over_limit_falls_back(self):
        task = "hi"  # limit = 400
        stub = _ChatOnceStub(content="a" * 401)
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            self.assertEqual(_refine(None, task), task)

    def test_runaway_limit_scales_with_task_length(self):
        # For a longer task the limit is len(task) * 6, not the 400 floor.
        task = "x" * 100  # limit = max(400, 600) = 600
        accepted = "b" * 600
        stub = _ChatOnceStub(content=accepted)
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", stub):
            self.assertEqual(_refine(None, task), accepted)

        over = _ChatOnceStub(content="b" * 601)
        with mock.patch.object(gateway, "GATEWAY_REFINE_ENABLED", True), \
                mock.patch.object(providers, "chat_once", over):
            self.assertEqual(_refine(None, task), task)


if __name__ == "__main__":
    unittest.main()
