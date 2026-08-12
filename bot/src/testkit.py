"""Shared test helpers. Imported by the test files, never by the app.

WHY THIS EXISTS: api.py imports Flask at module scope, so every test that
touches it dies with ModuleNotFoundError on a box without Flask. That was fixed
once in test_exit_guarantee.py by inlining a stub - and then test_coverage_gaps.py
was written, imported api, and hit the identical wall on devzone.

A fix that lives inside one file is not a fix, it is a workaround with a
countdown. One import, everywhere.
"""
from __future__ import annotations

import sys
import types


def stub_flask_if_missing():
    """Make `import api` work without Flask. No-op when Flask is installed."""
    try:
        import flask  # noqa: F401
        return False
    except ImportError:
        pass

    class _App:
        def __init__(self, *a, **k):
            pass

        def _dec(self, *a, **k):
            return lambda fn: fn

        route = get = post = after_request = before_request = _dec

    mod = types.ModuleType("flask")
    mod.Flask = _App
    mod.request = types.SimpleNamespace(
        get_json=lambda *a, **k: {}, args={}, headers={}, method="GET", path="/")
    mod.jsonify = lambda *a, **k: (a[0] if len(a) == 1 else k)
    sys.modules["flask"] = mod
    return True
