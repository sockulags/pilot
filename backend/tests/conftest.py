"""Shared test setup.

Isolate every test run from the developer's real model settings file
(data/model_settings.json): a settings file on the dev machine must never
change routing behaviour under test. The env var is set BEFORE application
modules import model_settings (conftest loads first), and the cache is reset
per test in case a test writes settings itself.
"""

import os
import tempfile

_ISOLATED = os.path.join(
    tempfile.mkdtemp(prefix="pilot-test-settings-"), "model_settings.json"
)
os.environ.setdefault("MODEL_SETTINGS_FILE", _ISOLATED)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_model_settings_cache():
    import model_settings

    def _clean():
        model_settings.reset_cache_for_tests()
        try:
            os.remove(model_settings.MODEL_SETTINGS_FILE)
        except OSError:
            pass

    _clean()
    yield
    _clean()
