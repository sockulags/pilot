import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class SkillLibraryTests(unittest.TestCase):
    """Skill files load + parse, and retrieval ranks by embedding similarity."""

    def test_seed_skills_load_and_parse(self):
        import skill_library
        skill_library._skills = None  # force reload
        skills = skill_library.load_skills()
        names = {s.name for s in skills}
        for expected in ("github-issues", "describe-project", "find-user-file", "web-lookup"):
            self.assertIn(expected, names)
        gh = next(s for s in skills if s.name == "github-issues")
        self.assertTrue(gh.triggers)  # frontmatter parsed
        self.assertIn("github_issues", gh.body)  # body tells which tool to use

    def test_search_ranks_relevant_skill_first(self):
        import skill_library

        # Deterministic fake embedder: vector keyed on whether GitHub words appear.
        async def fake_embed(text, *, is_query):
            t = text.lower()
            return [1.0, 0.0] if ("issue" in t or "github" in t or "pull request" in t) else [0.0, 1.0]

        skill_library._skills = None
        skill_library._embedded = False
        with mock.patch.object(skill_library, "_embed", new=fake_embed):
            hits = asyncio.run(skill_library.search_skills("how many open issues in my repo", top_k=1))
        self.assertTrue(hits)
        self.assertEqual("github-issues", hits[0].name)
        skill_library._skills = None
        skill_library._embedded = False


class OffloadDecisionTests(unittest.TestCase):
    """Local-first: code turns offload only on an explicit signal."""

    def test_auto_code_stays_local(self):
        from agents.orchestrator import should_offload_code
        self.assertFalse(should_offload_code("auto", "lägg till en funktion i utils.py"))

    def test_forced_code_route_offloads(self):
        from agents.orchestrator import should_offload_code
        self.assertTrue(should_offload_code("code", "lägg till en funktion"))

    def test_explicit_offload_keyword_offloads(self):
        from agents.orchestrator import should_offload_code
        self.assertTrue(should_offload_code("auto", "använd codex för att fixa det här"))
        self.assertTrue(should_offload_code("auto", "offloada det till claude"))


class FriendlyAgentErrorTests(unittest.TestCase):
    def test_usage_limit_is_humanised(self):
        from api.ws import _friendly_agent_error
        msg = _friendly_agent_error("{'message': \"You've hit your usage limit.\"}")
        self.assertIsNotNone(msg)
        self.assertIn("lokalt", msg)

    def test_codex_exited_no_events_is_humanised(self):
        from api.ws import _friendly_agent_error
        self.assertIsNotNone(_friendly_agent_error("[Codex exited 2 with no events]"))

    def test_normal_text_passes_through(self):
        from api.ws import _friendly_agent_error
        self.assertIsNone(_friendly_agent_error("Edited file utils.py and ran the tests."))


if __name__ == "__main__":
    unittest.main()
