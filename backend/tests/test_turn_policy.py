import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TurnPolicyTests(unittest.TestCase):
    def test_build_task_context_resolves_swedish_followup_to_standalone_search_query(self):
        from agents.turn_policy import build_task_context

        prior = [
            {
                "role": "user",
                "content": (
                    "Kan du hämta ut vilken temperatur det varit i Örebro under maj "
                    "månad, sätta ihop en snygg visuell graf över detta och visa det i "
                    "en HTML-fil?"
                ),
            },
            {"role": "assistant", "content": "Jag behöver hämta historisk väderdata."},
            {"role": "user", "content": "För att förtydliga så vill jag ha dag för dag."},
        ]

        ctx = build_task_context(prior, "Jag kan vänta. Sätt igång")

        self.assertTrue(ctx.needs_tools)
        self.assertEqual("research_and_create_file", ctx.intent)
        self.assertIn("Örebro", ctx.standalone_task)
        self.assertIn("maj", ctx.standalone_task.lower())
        self.assertIn("dag", ctx.standalone_task.lower())
        self.assertIn("HTML", ctx.standalone_task)
        self.assertIn("Örebro", ctx.search_query)
        self.assertIn("daily", ctx.search_query.lower())

    def test_deterministic_route_for_file_creating_research_is_computer(self):
        from agents.turn_policy import deterministic_route

        decision = deterministic_route(
            [],
            "Kan du hämta temperaturdata för Örebro och visa det i en HTML-fil?",
            project=None,
            model_mode="auto",
        )

        self.assertIsNotNone(decision)
        self.assertEqual("computer", decision["route"])
        self.assertIn("HTML", decision["task"])

    def test_deterministic_route_for_git_status_is_computer_with_project_warning(self):
        from agents.turn_policy import deterministic_route

        decision = deterministic_route([], "Kör git status i projektet", project=None)

        self.assertEqual("computer", decision["route"])
        self.assertIn("git status", decision["task"])
        self.assertIn("No project folder", decision["thinking"])

    def test_repo_flow_question_mentions_saved_session_without_creating_file(self):
        from agents.turn_policy import build_task_context

        ctx = build_task_context(
            [],
            (
                "Gå igenom det här projektets backendflöde för en användarfråga "
                "som kräver webbsökning eller ett shell-kommando. Förklara exakt "
                "hur meddelandet routas från WebSocket till modellval, tool-call "
                "och sparad session. Identifiera två svaga punkter och föreslå "
                "tester som skulle fånga dem."
            ),
        )

        self.assertFalse(ctx.creates_file)
        self.assertFalse(ctx.requires_current_sources)
        self.assertTrue(ctx.needs_tools)
        self.assertEqual("project_analysis", ctx.intent)

    def test_sanitize_final_reply_replaces_pseudo_tool_code(self):
        from agents.turn_policy import sanitize_final_reply

        reply = 'Jag söker nu:\n<tool_code>\nweb_search(query="SMHI Örebro")\n</tool_code>'

        cleaned = sanitize_final_reply(reply, had_actions=False, needs_tools=True)

        self.assertNotIn("<tool_code>", cleaned)
        self.assertNotIn("web_search(", cleaned)
        self.assertIn("Jag kunde inte utföra", cleaned)

    def test_choose_coordinator_model_uses_stronger_model_for_research_when_auto(self):
        from agents.turn_policy import choose_coordinator_model

        ctx = mock.Mock(requires_current_sources=True, preferred_model="gpt-oss:20b")

        self.assertEqual("gpt-oss:20b", choose_coordinator_model("auto", ctx))

    def test_task_contract_intent_maps_core_tool_backed_intents(self):
        from agents.turn_policy import build_task_context, task_contract_intent

        self.assertEqual("research", task_contract_intent(build_task_context([], "Research Volvo")))
        self.assertEqual("create_file", task_contract_intent(build_task_context([], "Skapa en rapportfil")))
        self.assertEqual(
            "project_analysis",
            task_contract_intent(build_task_context([], "Förklara det här projektets backendflöde")),
        )
        self.assertEqual("run_command", task_contract_intent(build_task_context([], "Kör git status")))
        self.assertIsNone(task_contract_intent(build_task_context([], "Öppna Notepad")))


class StoreMetadataTests(unittest.TestCase):
    def test_save_session_skips_empty_draft_sessions(self):
        import store

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp):
                store.save_session("empty", [], 0)

            self.assertFalse(os.path.exists(os.path.join(tmp, "empty.json")))

    def test_session_roundtrip_preserves_message_metadata(self):
        import store

        messages = [
            {
                "role": "assistant",
                "content": "Klart",
                "meta": {
                    "turn": 1,
                    "route": "computer",
                    "model": "gpt-oss:20b",
                    "final_source": "web_research",
                    "tools": ["web_research"],
                    "status": "done",
                },
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(store, "SESSIONS_DIR", tmp):
                store.save_session("sess1", messages, 1)
                loaded = store.load_session("sess1")

        self.assertEqual(messages, loaded["messages"])


if __name__ == "__main__":
    unittest.main()
