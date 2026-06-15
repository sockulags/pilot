import asyncio
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


async def _fake_embed(text, *, is_query):
    """Deterministic keyword embedding so tests need no Ollama."""
    t = text.lower()
    return [
        1.0 if any(w in t for w in ("lucas", "heter", "namn", "name")) else 0.0,
        1.0 if any(w in t for w in ("projekt", "pilot")) else 0.0,
        1.0 if any(w in t for w in ("frankrike", "france", "huvudstad")) else 0.0,
    ]


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        os.unlink(self.tmp.name)  # start empty
        self._patches = [
            mock.patch("memory.MEMORY_FILE", self.tmp.name),
            mock.patch("memory._embed", _fake_embed),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        if os.path.exists(self.tmp.name):
            os.unlink(self.tmp.name)

    def test_cosine_identical_and_orthogonal(self):
        from memory import _cosine

        self.assertAlmostEqual(1.0, _cosine([1.0, 0.0], [1.0, 0.0]))
        self.assertAlmostEqual(0.0, _cosine([1.0, 0.0], [0.0, 1.0]))
        self.assertEqual(0.0, _cosine([], [1.0]))

    def test_save_and_recall_relevant_memory(self):
        asyncio.run(self._save_and_recall())

    async def _save_and_recall(self):
        from memory import save_memory, search_memories

        await save_memory("Jag heter Lucas.")
        await save_memory("Projektet heter Pilot.")

        hits = await search_memories("Vad heter jag?")
        self.assertTrue(hits)
        self.assertEqual("Jag heter Lucas.", hits[0]["text"])

        # An unrelated query retrieves nothing above threshold.
        self.assertEqual([], await search_memories("Vad är huvudstaden i Frankrike?"))

    def test_save_dedupes_near_identical(self):
        asyncio.run(self._save_dedupes())

    async def _save_dedupes(self):
        from memory import save_memory, list_memories

        id1 = await save_memory("Jag heter Lucas.")
        id2 = await save_memory("Jag heter Lucas.")
        self.assertEqual(id1, id2)
        self.assertEqual(1, len(list_memories()))

    def test_delete_memory(self):
        asyncio.run(self._delete())

    async def _delete(self):
        from memory import save_memory, delete_memory, list_memories

        mem_id = await save_memory("Projektet heter Pilot.")
        self.assertTrue(delete_memory(mem_id))
        self.assertEqual([], list_memories())
        self.assertFalse(delete_memory("nope"))


if __name__ == "__main__":
    unittest.main()
