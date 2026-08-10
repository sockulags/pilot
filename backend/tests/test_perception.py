"""Direct tests for agents/perception.py, the Set-of-Marks screen perception module.

The rest of the suite (test_agent_safety.py, test_coordinator.py,
test_freshness.py) mocks perceive_screen/enumerate_elements at the call
boundary, so perception's own logic — element enumeration, annotation, the
loopback capture guard and the observation bookkeeping — was never driven.
This file drives it directly (issue #128).

No live desktop is touched. The UIA traversal runs against a fake control tree
(_FakeControl below exposes only the attributes enumerate_elements reads) with
_foreground_control patched to return its root and a stub module standing in for
uiautomation, so the tests behave the same on a headless runner as on a Windows
desktop. The headless-browser capture fakes subprocess.run the same way
test_traceability.py's CodexCliResolverTests fakes the Codex CLI. Only
annotate_screenshot uses real dependencies: PIL draws on a small PNG generated
in-test, because that is cheaper than mocking it and checks the real output.
"""

import base64
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

EDGE_RELATIVE = "Microsoft/Edge/Application/msedge.exe"
CHROME_RELATIVE = "Google/Chrome/Application/chrome.exe"


def _element(element_id, name="OK", control_type="ButtonControl", rect=(0, 0, 20, 10)):
    from agents.perception import Element

    left, top, right, bottom = rect
    return Element(
        id=element_id,
        name=name,
        control_type=control_type,
        rect=rect,
        center=((left + right) // 2, (top + bottom) // 2),
    )


def _png_b64(size=(160, 120), color=(12, 12, 16)) -> str:
    """A real PNG, base64-encoded exactly as tools.screenshot() would return it."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _FakeRect:
    """The subset of uiautomation's Rect that the traversal calls."""

    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom

    def width(self):
        return self.right - self.left

    def height(self):
        return self.bottom - self.top

    def xcenter(self):
        return (self.left + self.right) // 2

    def ycenter(self):
        return (self.top + self.bottom) // 2


class _FakeControl:
    """Stand-in for a uiautomation Control node."""

    def __init__(self, control_type, name="", rect=(0, 0, 20, 10), offscreen=False, children=()):
        self.ControlTypeName = control_type
        self.Name = name
        self.BoundingRectangle = _FakeRect(*rect) if rect else None
        self.IsOffscreen = offscreen
        self._children = list(children)

    def GetChildren(self):
        return list(self._children)


class _RaisingControl:
    """A node whose rect access fails, as a control disconnected mid-walk would."""

    ControlTypeName = "ButtonControl"
    Name = "explodes"
    IsOffscreen = False

    @property
    def BoundingRectangle(self):
        raise RuntimeError("UIA element is no longer available")

    def GetChildren(self):
        return []


@contextlib.contextmanager
def _fake_uia(root=None, foreground_error=None):
    """Run enumerate_elements against a fake tree instead of the live desktop.

    A stub module shadows uiautomation so the import guard succeeds without the
    real dependency, and _foreground_control returns the given root (or raises).
    """
    from agents import perception

    patched_foreground = (
        mock.patch.object(perception, "_foreground_control", side_effect=foreground_error)
        if foreground_error is not None
        else mock.patch.object(perception, "_foreground_control", return_value=root)
    )
    with mock.patch.dict(sys.modules, {"uiautomation": ModuleType("uiautomation")}):
        with patched_foreground:
            yield


class _PerceptionStateTestCase(unittest.TestCase):
    """Snapshots the module-level element cache and observation record."""

    def setUp(self):
        from agents import perception

        self.perception = perception
        self._saved = (
            dict(perception._LAST_ELEMENTS),
            perception._observation_counter,
            perception._current_observation_id,
            perception._observation_timestamp,
            perception._observation_window,
        )

    def tearDown(self):
        elements, counter, observation_id, timestamp, window = self._saved
        self.perception._LAST_ELEMENTS.clear()
        self.perception._LAST_ELEMENTS.update(elements)
        self.perception._observation_counter = counter
        self.perception._current_observation_id = observation_id
        self.perception._observation_timestamp = timestamp
        self.perception._observation_window = window


class ElementsTextTests(unittest.TestCase):
    def test_empty_element_list_tells_the_model_to_fall_back_to_coordinates(self):
        from agents.perception import elements_text

        text = elements_text([])

        self.assertIn("No interactive UI elements were detected", text)
        self.assertIn("click(x, y)", text)

    def test_elements_render_one_numbered_line_each_in_input_order(self):
        from agents.perception import elements_text

        lines = elements_text([
            _element(1, "Save", "ButtonControl", rect=(10, 20, 110, 60)),
            _element(2, "Search the web", "EditControl", rect=(0, 0, 200, 30)),
        ]).splitlines()

        self.assertIn("click_element(element_id)", lines[0])
        self.assertEqual("[1] Save (ButtonControl) center=(60,40)", lines[1])
        self.assertEqual("[2] Search the web (EditControl) center=(100,15)", lines[2])
        self.assertEqual(3, len(lines))


class AnnotateScreenshotTests(unittest.TestCase):
    def test_screenshot_is_returned_untouched_when_there_are_no_elements(self):
        from agents.perception import annotate_screenshot

        image_b64 = _png_b64()

        self.assertEqual(image_b64, annotate_screenshot(image_b64, []))

    def test_undecodable_input_is_returned_untouched_instead_of_raising(self):
        from agents.perception import annotate_screenshot

        for label, image_b64 in (
            ("not base64 at all", "definitely-not-base64"),
            ("valid base64, not an image", base64.b64encode(b"plain text").decode()),
        ):
            with self.subTest(label):
                self.assertEqual(image_b64, annotate_screenshot(image_b64, [_element(1)]))

    def test_annotated_result_is_a_png_of_the_same_size_with_a_label_drawn(self):
        from PIL import Image

        from agents.perception import annotate_screenshot

        image_b64 = _png_b64(size=(160, 120))
        annotated = annotate_screenshot(image_b64, [_element(1, "Save", rect=(10, 10, 60, 40))])

        self.assertNotEqual(image_b64, annotated)
        img = Image.open(io.BytesIO(base64.b64decode(annotated)))
        self.assertEqual("PNG", img.format)
        self.assertEqual((160, 120), img.size)
        # The label chip is filled before the number is drawn on top of it.
        self.assertEqual((220, 32, 32), img.convert("RGB").getpixel((11, 20)))

    def test_annotation_still_works_when_the_truetype_font_is_unavailable(self):
        from PIL import Image

        from agents import perception

        real_truetype = perception.ImageFont.truetype

        def without_arial(font, *args, **kwargs):
            # Only the named system font is missing; PIL's own bundled fallback
            # loads through the same entry point and must keep working.
            if isinstance(font, str):
                raise OSError("cannot open resource")
            return real_truetype(font, *args, **kwargs)

        image_b64 = _png_b64()
        with mock.patch.object(perception.ImageFont, "truetype", new=without_arial):
            annotated = perception.annotate_screenshot(image_b64, [_element(1)])

        self.assertNotEqual(image_b64, annotated)
        self.assertEqual((160, 120), Image.open(io.BytesIO(base64.b64decode(annotated))).size)


class InstalledBrowserExecutableTests(unittest.TestCase):
    """Candidate order is Edge before Chrome, and x86 before 64-bit before local."""

    @contextlib.contextmanager
    def _environment(self, root: Path, present: tuple[str, ...]):
        for relative in present:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"")
        with mock.patch.dict(
            os.environ,
            {
                "PROGRAMFILES(X86)": str(root / "pf86"),
                "PROGRAMFILES": str(root / "pf"),
                "LOCALAPPDATA": str(root / "local"),
            },
            clear=False,
        ):
            yield

    def test_returns_none_when_no_candidate_path_exists(self):
        from agents.perception import _installed_browser_executable

        with tempfile.TemporaryDirectory() as tmp:
            with self._environment(Path(tmp), present=()):
                self.assertIsNone(_installed_browser_executable())

    def test_returns_none_when_the_location_environment_variables_are_unset(self):
        from agents.perception import _installed_browser_executable

        with mock.patch.dict(os.environ, {}, clear=False):
            for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
                os.environ.pop(name, None)
            self.assertIsNone(_installed_browser_executable())

    def test_first_existing_candidate_wins_in_the_documented_order(self):
        from agents.perception import _installed_browser_executable

        cases = (
            (
                "x86 Edge outranks everything else",
                (f"pf86/{EDGE_RELATIVE}", f"pf/{EDGE_RELATIVE}", f"pf/{CHROME_RELATIVE}"),
                f"pf86/{EDGE_RELATIVE}",
            ),
            (
                "64-bit Edge outranks any Chrome",
                (f"pf/{EDGE_RELATIVE}", f"pf/{CHROME_RELATIVE}", f"local/{CHROME_RELATIVE}"),
                f"pf/{EDGE_RELATIVE}",
            ),
            (
                "program-files Chrome outranks the per-user install",
                (f"pf/{CHROME_RELATIVE}", f"local/{CHROME_RELATIVE}"),
                f"pf/{CHROME_RELATIVE}",
            ),
            (
                "a per-user Chrome is still found",
                (f"local/{CHROME_RELATIVE}",),
                f"local/{CHROME_RELATIVE}",
            ),
        )
        for label, present, expected in cases:
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with self._environment(root, present):
                    self.assertEqual(root / expected, _installed_browser_executable())


class CaptureLocalWebpageTests(unittest.TestCase):
    """The loopback guard and the headless-browser subprocess branches.

    test_agent_safety.py pins the headline case (an external https URL is
    refused before a browser can launch); this covers the remaining rejected
    shapes and every branch after the guard, with subprocess.run faked.
    """

    def _fake_run(self, returncode=0, stdout="", stderr="", write_screenshot=True):
        def run(command, **_kwargs):
            if write_screenshot:
                target = next(
                    arg.split("=", 1)[1] for arg in command if arg.startswith("--screenshot=")
                )
                Path(target).write_bytes(base64.b64decode(_png_b64()))
            self.command = list(command)
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        return run

    def test_rejects_urls_that_are_not_plain_loopback_http(self):
        from agents import perception

        rejected = (
            ("non-http scheme", "file:///C:/Windows/win.ini"),
            ("ftp scheme", "ftp://localhost/page"),
            ("external host", "https://example.com/"),
            ("host that merely looks local", "http://localhost.evil.example/"),
            ("embedded username", "http://admin@localhost:3000/"),
            ("embedded credentials", "http://admin:hunter2@127.0.0.1:3000/"),
        )
        with mock.patch.object(
            perception,
            "_installed_browser_executable",
            side_effect=AssertionError("no browser may be resolved for a rejected URL"),
        ):
            for label, url in rejected:
                with self.subTest(label):
                    with self.assertRaisesRegex(ValueError, "loopback"):
                        perception.capture_local_webpage(url)

    def test_raises_when_no_supported_browser_is_installed(self):
        from agents import perception

        with mock.patch.object(perception, "_installed_browser_executable", return_value=None), \
            mock.patch(
                "agents.perception.subprocess.run",
                side_effect=AssertionError("nothing may be launched"),
            ):
            with self.assertRaisesRegex(RuntimeError, "No supported local Chromium browser"):
                perception.capture_local_webpage("http://localhost:3000/")

    def test_successful_render_returns_a_png_and_a_descriptive_message(self):
        from PIL import Image

        from agents import perception

        with mock.patch.object(
            perception, "_installed_browser_executable", return_value=Path("C:/edge/msedge.exe")
        ), mock.patch("agents.perception.subprocess.run", new=self._fake_run()):
            image_b64, message = perception.capture_local_webpage("http://localhost:3000/ui")

        self.assertEqual("PNG", Image.open(io.BytesIO(base64.b64decode(image_b64))).format)
        self.assertEqual("Captured local webpage directly: http://localhost:3000/ui", message)
        self.assertIn("--headless=new", self.command)
        self.assertEqual("http://localhost:3000/ui", self.command[-1])

    def test_reports_the_process_output_when_the_browser_fails(self):
        from agents import perception

        cases = (
            (
                "non-zero exit reports stderr",
                self._fake_run(returncode=1, stderr="msedge: could not connect", stdout="ignored"),
                "could not connect",
            ),
            (
                "clean exit without a screenshot reports stdout",
                self._fake_run(stdout="page load timed out", write_screenshot=False),
                "page load timed out",
            ),
            (
                "a silent failure still names the failure",
                self._fake_run(returncode=1, write_screenshot=False),
                "browser capture failed",
            ),
        )
        for label, fake_run, expected in cases:
            with self.subTest(label):
                with mock.patch.object(
                    perception,
                    "_installed_browser_executable",
                    return_value=Path("C:/edge/msedge.exe"),
                ), mock.patch("agents.perception.subprocess.run", new=fake_run):
                    with self.assertRaisesRegex(RuntimeError, expected):
                        perception.capture_local_webpage("http://127.0.0.1:8000/")


class ActiveWindowTitleSafeTests(unittest.TestCase):
    def test_returns_the_foreground_title(self):
        from agents.perception import _active_window_title_safe

        with mock.patch("tools.os_tools.active_window_title", return_value="Untitled - Notepad"):
            self.assertEqual("Untitled - Notepad", _active_window_title_safe())

    def test_a_missing_title_becomes_an_empty_string(self):
        from agents.perception import _active_window_title_safe

        with mock.patch("tools.os_tools.active_window_title", return_value=None):
            self.assertEqual("", _active_window_title_safe())

    def test_a_failing_lookup_is_swallowed(self):
        from agents.perception import _active_window_title_safe

        with mock.patch(
            "tools.os_tools.active_window_title", side_effect=RuntimeError("no window manager")
        ):
            self.assertEqual("", _active_window_title_safe())


class ElementCacheTests(_PerceptionStateTestCase):
    """get_element / get_element_observation / invalidate_observation."""

    def setUp(self):
        super().setUp()
        self.perception._LAST_ELEMENTS.clear()
        self.perception._LAST_ELEMENTS[1] = _element(1, "Save")
        self.perception._current_observation_id = 7

    def test_a_cached_id_resolves_to_its_element(self):
        element = self.perception.get_element(1)

        self.assertIsNotNone(element)
        self.assertEqual("Save", element.name)

    def test_a_numeric_string_id_is_coerced(self):
        self.assertIs(self.perception._LAST_ELEMENTS[1], self.perception.get_element("1"))

    def test_an_unknown_id_resolves_to_none(self):
        self.assertIsNone(self.perception.get_element(99))

    def test_an_id_that_is_not_a_number_resolves_to_none_instead_of_raising(self):
        for label, element_id in (("text", "seventeen"), ("none", None), ("object", object())):
            with self.subTest(label):
                self.assertIsNone(self.perception.get_element(element_id))

    def test_a_cached_id_reports_the_current_observation(self):
        self.assertEqual(7, self.perception.get_element_observation(1))

    def test_a_stale_id_reports_no_observation(self):
        self.assertIsNone(self.perception.get_element_observation(99))

    def test_no_observation_is_reported_while_the_cache_is_invalidated(self):
        self.perception._current_observation_id = 0

        self.assertIsNone(self.perception.get_element_observation(1))

    def test_invalidation_drops_the_cache_and_resets_the_observation_id(self):
        self.perception.invalidate_observation()

        self.assertEqual({}, self.perception._LAST_ELEMENTS)
        self.assertEqual(0, self.perception.current_observation_id())
        self.assertIsNone(self.perception.get_element(1))


class EnumerateElementsTests(unittest.TestCase):
    """The bounded UIA walk, driven by a fake control tree (no live desktop)."""

    def test_returns_nothing_and_logs_when_uiautomation_is_unavailable(self):
        from agents import perception

        with mock.patch.dict(sys.modules, {"uiautomation": None}), mock.patch.object(
            perception,
            "_foreground_control",
            side_effect=AssertionError("the tree must not be walked"),
        ):
            with self.assertLogs(perception.logger, level="INFO") as logs:
                self.assertEqual([], perception.enumerate_elements())

        self.assertIn("uiautomation unavailable", "\n".join(logs.output))

    def test_returns_nothing_and_logs_when_the_foreground_control_cannot_be_resolved(self):
        from agents import perception

        with _fake_uia(foreground_error=RuntimeError("no foreground window")):
            with self.assertLogs(perception.logger, level="WARNING") as logs:
                self.assertEqual([], perception.enumerate_elements())

        self.assertIn("Could not resolve foreground control", "\n".join(logs.output))

    def test_collects_only_visible_sized_interactive_controls_below_the_root(self):
        from agents import perception

        root = _FakeControl(
            "ButtonControl",  # the root itself is never collected (depth 0)
            name="root button",
            children=[
                _FakeControl("ButtonControl", name="Save"),
                _FakeControl("TextControl", name="not interactive"),
                _FakeControl("ButtonControl", name="hidden", offscreen=True),
                _FakeControl("ButtonControl", name="collapsed", rect=(5, 5, 5, 5)),
                _FakeControl("ButtonControl", name="no rect", rect=None),
                _RaisingControl(),
                _FakeControl("PaneControl", children=[
                    _FakeControl("HyperlinkControl", name="Nested link"),
                ]),
            ],
        )
        with _fake_uia(root):
            elements = perception.enumerate_elements()

        self.assertEqual({"Save", "Nested link"}, {el.name for el in elements})
        self.assertEqual([1, 2], sorted(el.id for el in elements))

    def test_element_fields_come_from_the_control_rect_and_name(self):
        from agents import perception

        root = _FakeControl("WindowControl", children=[
            _FakeControl("EditControl", name="  Search  ", rect=(10, 20, 110, 60)),
        ])
        with _fake_uia(root):
            (element,) = perception.enumerate_elements()

        self.assertEqual(1, element.id)
        self.assertEqual("Search", element.name)
        self.assertEqual("EditControl", element.control_type)
        self.assertEqual((10, 20, 110, 60), element.rect)
        self.assertEqual((60, 40), element.center)

    def test_long_names_are_truncated_and_unnamed_controls_fall_back_to_their_type(self):
        from agents import perception

        root = _FakeControl("WindowControl", children=[
            _FakeControl("ButtonControl", name="x" * 200),
            _FakeControl("CheckBoxControl", name="   "),
        ])
        with _fake_uia(root):
            names = {el.name for el in perception.enumerate_elements()}

        self.assertEqual({"x" * 80, "CheckBoxControl"}, names)

    def test_collection_stops_at_max_elements(self):
        from agents import perception

        root = _FakeControl("WindowControl", children=[
            _FakeControl("ButtonControl", name=f"button {i}") for i in range(20)
        ])
        with _fake_uia(root):
            elements = perception.enumerate_elements(max_elements=3)

        self.assertEqual(3, len(elements))

    def test_traversal_stops_after_the_node_budget_is_spent(self):
        from agents import perception

        root = _FakeControl("WindowControl", children=[
            _FakeControl("ButtonControl", name=f"button {i}")
            for i in range(perception._MAX_NODES + 10)
        ])
        with _fake_uia(root):
            elements = perception.enumerate_elements(max_elements=10_000)

        # The root consumes one of the visits, so the budget yields one fewer element.
        self.assertEqual(perception._MAX_NODES - 1, len(elements))

    def test_children_below_the_depth_limit_are_not_visited(self):
        from agents import perception

        too_deep = _FakeControl("ButtonControl", name="past the limit")
        node = _FakeControl("ButtonControl", name="at the limit", children=[too_deep])
        for _ in range(perception._MAX_DEPTH - 1):
            node = _FakeControl("PaneControl", children=[node])
        root = _FakeControl("WindowControl", children=[node])

        with _fake_uia(root):
            elements = perception.enumerate_elements()

        self.assertEqual(["at the limit"], [el.name for el in elements])


class PerceiveScreenTests(_PerceptionStateTestCase):
    """Capture + enumerate + annotate, with the screen and the UIA walk faked."""

    @contextlib.contextmanager
    def _fake_capture(self, elements, window="Untitled - Notepad"):
        with mock.patch("tools.screenshot", return_value=_png_b64()), \
            mock.patch.object(self.perception, "enumerate_elements", return_value=elements), \
            mock.patch.object(self.perception, "_active_window_title_safe", return_value=window):
            yield

    def test_perception_caches_elements_and_records_the_observation(self):
        import time

        elements = [_element(1, "Save"), _element(2, "Cancel", rect=(30, 0, 90, 20))]
        before = self.perception.current_observation_id()

        with self._fake_capture(elements):
            annotated, returned, text = self.perception.perceive_screen()

        self.assertEqual(elements, returned)
        self.assertEqual(self.perception.elements_text(elements), text)
        self.assertNotEqual(_png_b64(), annotated)
        self.assertEqual({1: elements[0], 2: elements[1]}, self.perception._LAST_ELEMENTS)
        self.assertGreater(self.perception.current_observation_id(), before)
        self.assertEqual("Untitled - Notepad", self.perception.observation_active_window())
        self.assertAlmostEqual(time.time(), self.perception.observation_timestamp(), delta=10)

    def test_each_perception_supersedes_the_previous_observation(self):
        with self._fake_capture([_element(1, "Save")]):
            self.perception.perceive_screen()
            first = self.perception.current_observation_id()
            self.perception.perceive_screen()

        self.assertEqual(first + 1, self.perception.current_observation_id())

    def test_an_empty_perception_leaves_the_screenshot_unannotated(self):
        with self._fake_capture([], window=""):
            annotated, elements, text = self.perception.perceive_screen()

        self.assertEqual(_png_b64(), annotated)
        self.assertEqual([], elements)
        self.assertIn("No interactive UI elements were detected", text)
        self.assertEqual({}, self.perception._LAST_ELEMENTS)
        self.assertGreater(self.perception.current_observation_id(), 0)


if __name__ == "__main__":
    unittest.main()
