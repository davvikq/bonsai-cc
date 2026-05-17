"""Web renderer -- SVG bonsai over HTTP / Server-Sent Events.

Why this exists: the ASCII renderer is structurally correct but
visually too sparse to deliver the "a beautiful bonsai grows as
you work" promise. The web renderer is a NEW projection of the
same ``TreeState``, not a rewrite of the growth engine. The
runner doesn't know whether it's pushing state to a Textual app
or a web broadcaster -- both honour the same one-method shape
(``set_state(state, last_event_name=...)``).

Why aiohttp (over stdlib http.server + DIY async): the daemon is
already asyncio-first; bolting a sync server onto an async event
loop is fiddly and offers no real win. aiohttp is a single small
dep, async-native, and ships a test client we use in unit tests.
"""

from bonsai_cc.web.broadcaster import WebBroadcaster
from bonsai_cc.web.pipeline import run_web_pipeline
from bonsai_cc.web.server import build_app

__all__ = ["WebBroadcaster", "build_app", "run_web_pipeline"]
