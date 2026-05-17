"""Web renderer -- SVG bonsai over HTTP / Server-Sent Events.

This is the live surface. Twelve per-theme SVG renderers project the
same ``TreeState`` the growth engine emits; the broadcaster fans
each new state out to subscribed browser tabs over SSE. The runner
is renderer-agnostic on purpose -- anything that satisfies the
``set_state(state, last_event_name=...)`` shape can take its place
(the test suite substitutes a recorder).

Why aiohttp (over stdlib http.server + DIY async): the daemon is
already asyncio-first; bolting a sync server onto an async event
loop is fiddly and offers no real win. aiohttp is a single small
dep, async-native, and ships a test client we use in unit tests.
"""

from bonsai_cc.web.broadcaster import WebBroadcaster
from bonsai_cc.web.pipeline import run_web_pipeline
from bonsai_cc.web.server import build_app

__all__ = ["WebBroadcaster", "build_app", "run_web_pipeline"]
