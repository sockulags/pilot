import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class TurnDiagnosticsTests(unittest.TestCase):
    def test_append_turn_diagnostic_writes_jsonl_under_data(self):
        import diagnostics

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "turn_diagnostics.jsonl")
            with mock.patch.object(diagnostics, "DIAGNOSTICS_FILE", path):
                diagnostics.append_turn_diagnostic(
                    session_id="s1",
                    turn=2,
                    route="computer",
                    model="gemma4:latest",
                    events=[
                        {
                            "type": "action",
                            "tool": "web_research",
                            "args": {"query": "Volvo Cars"},
                        },
                        {
                            "type": "error",
                            "content": "web_search requires argument(s): query",
                        },
                    ],
                    status="done",
                    final_source="web_research",
                )

            with open(path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f]

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual("s1", row["session_id"])
        self.assertEqual(2, row["turn"])
        self.assertEqual("computer", row["route"])
        self.assertEqual("gemma4:latest", row["model"])
        self.assertEqual("done", row["status"])
        self.assertEqual("web_research", row["final_source"])
        self.assertEqual("web_research", row["tools"][0]["tool"])
        self.assertEqual("error", row["errors"][0]["type"])


if __name__ == "__main__":
    unittest.main()
