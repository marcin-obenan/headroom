"""Shared Gemini model id for opt-in live API tests.

Override with ``HEADROOM_LIVE_GEMINI_MODEL`` when Google deprecates the default.
Run live Gemini tests with::

    pytest -m real_llm tests/test_proxy_gemini_integration.py tests/test_proxy_gemini_native_integration.py -v
"""

from __future__ import annotations

import os

GEMINI_LIVE_MODEL = os.environ.get("HEADROOM_LIVE_GEMINI_MODEL", "gemini-2.5-flash")
