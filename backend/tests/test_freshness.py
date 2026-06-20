"""Window/element freshness guards for desktop input tools (issue #40).

A perception captures the foreground window title alongside the element cache.
Before a click/type/hotkey runs, the live foreground window is compared to the
observed one; on a mismatch the action is refused and the cached element ids are
invalidated so click_element also fails until the screen is re-observed.

pyautogui and active_window_title are monkeypatched — no real desktop input.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class _FreshnessTestBase(unittest.TestCase):
    """Stubs perception state to simulate a perception captured in window "A"."""

    def setUp(self):
        from agents import perception
        from tools import input as input_tools
        from tools import os_tools

        self.perception = perception
        self.input_tools = input_tools
        self.os_tools = os_tools

        # Snapshot mutable module state so each test restores it.
        self._saved_elements = dict(perception._LAST_ELEMENTS)
        self._saved_obs_id = perception._current_observation_id
        self._saved_obs_window = perception._observation_window
        self._saved_active = os_tools.active_window_title

        # Simulate a completed perception in window "A" with one element.
        perception._LAST_ELEMENTS.clear()
        perception._LAST_ELEMENTS[1] = perception.Element(
            id=1, name="OK", control_type="ButtonControl",
            rect=(0, 0, 10, 10), center=(5, 5),
        )
        perception._current_observation_id = 7
        perception._observation_window = "Untitled - Notepad"

        # Record pyautogui calls instead of performing them.
        self.calls: list[tuple] = []
        self._saved_pyautogui = input_tools.pyautogui
        input_tools.pyautogui = self._FakePyautogui(self.calls)

    def tearDown(self):
        self.perception._LAST_ELEMENTS.clear()
        self.perception._LAST_ELEMENTS.update(self._saved_elements)
        self.perception._current_observation_id = self._saved_obs_id
        self.perception._observation_window = self._saved_obs_window
        self.os_tools.active_window_title = self._saved_active
        self.input_tools.pyautogui = self._saved_pyautogui

    def _set_active_window(self, title: str):
        self.os_tools.active_window_title = lambda: title

    class _FakePyautogui:
        def __init__(self, calls):
            self._calls = calls

        def click(self, x, y, button="left"):
            self._calls.append(("click", x, y, button))

        def typewrite(self, text, interval=0.0):
            self._calls.append(("typewrite", text))

        def hotkey(self, *keys):
            self._calls.append(("hotkey", keys))

        def press(self, key):
            self._calls.append(("press", key))


class ClickElementFreshnessTests(_FreshnessTestBase):
    def test_click_element_works_when_window_unchanged(self):
        from tools.elements import click_element

        self._set_active_window("Untitled - Notepad")
        result = click_element(1)

        self.assertIn("Clicked element", result)
        self.assertEqual([("click", 5, 5, "left")], self.calls)

    def test_click_element_refuses_when_window_changed(self):
        from tools.elements import click_element

        self._set_active_window("Some Other App")
        result = click_element(1)

        self.assertIn("click_element failed", result)
        self.assertEqual([], self.calls)
        # The window switch invalidated the cached ids.
        self.assertEqual(0, self.perception.current_observation_id())
        self.assertIsNone(self.perception.get_element(1))

    def test_click_element_fails_for_stale_id(self):
        from tools.elements import click_element

        self._set_active_window("Untitled - Notepad")
        result = click_element(999)  # never perceived

        self.assertIn("click_element failed", result)
        self.assertEqual([], self.calls)


class KeyboardFreshnessTests(_FreshnessTestBase):
    def test_type_text_blocked_when_window_changed(self):
        from tools.input import type_text

        self._set_active_window("Some Other App")
        result = type_text("hej")

        self.assertIn("type_text failed", result)
        self.assertIn("active window changed", result)
        self.assertEqual([], self.calls)

    def test_type_text_allowed_when_window_matches(self):
        from tools.input import type_text

        self._set_active_window("Untitled - Notepad")
        result = type_text("hej")

        self.assertEqual("Typed: hej", result)
        self.assertEqual([("typewrite", "hej")], self.calls)

    def test_hotkey_blocked_when_window_changed(self):
        from tools.input import hotkey

        self._set_active_window("Some Other App")
        result = hotkey("ctrl", "s")

        self.assertIn("hotkey failed", result)
        self.assertEqual([], self.calls)

    def test_hotkey_allowed_when_window_matches(self):
        from tools.input import hotkey

        self._set_active_window("Untitled - Notepad")
        result = hotkey("ctrl", "s")

        self.assertEqual("Hotkey: ctrl+s", result)
        self.assertEqual([("hotkey", ("ctrl", "s"))], self.calls)

    def test_key_press_blocked_when_window_changed(self):
        from tools.input import key_press

        self._set_active_window("Some Other App")
        result = key_press("enter")

        self.assertIn("key_press failed", result)
        self.assertEqual([], self.calls)


class FreshnessHelperTests(_FreshnessTestBase):
    def test_no_block_before_any_perception(self):
        from agents.safety import desktop_action_freshness_reason

        self.perception.invalidate_observation()  # no current observation
        self._set_active_window("Anything")

        self.assertIsNone(desktop_action_freshness_reason("type_text"))

    def test_window_switch_between_perception_and_action(self):
        from agents.safety import target_window_changed

        self._set_active_window("Untitled - Notepad")
        self.assertFalse(target_window_changed())

        self._set_active_window("A Different Window")
        self.assertTrue(target_window_changed())

    def test_freshness_only_applies_to_window_targeted_tools(self):
        from agents.safety import desktop_action_freshness_reason

        self._set_active_window("Some Other App")
        # move_mouse / click / scroll are not window-targeted here.
        self.assertIsNone(desktop_action_freshness_reason("move_mouse"))
        self.assertIsNone(desktop_action_freshness_reason("scroll"))


if __name__ == "__main__":
    unittest.main()
