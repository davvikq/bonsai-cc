"""Server-side SVG renderer: shape, palette, real-session feed.

Mirrors the JS client in ``static/index.html``. These tests pin
the contract that both implementations honour:

* viewport is ``0 0 1000 800``
* trunk renders as a ``<path>`` fill, not a stroke
* every TreeState element type contributes geometry
* palette colours appear verbatim in the output
* a real-session journal produces a non-trivial SVG
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bonsai_cc.events.models import parse_event
from bonsai_cc.growth.apply import apply_all
from bonsai_cc.growth.state import (
    Branch,
    Flower,
    Offshoot,
    Root,
    Segment,
    TreeState,
    demo_tree,
)
from bonsai_cc.web.render import tokens
from bonsai_cc.web.svg_render import (
    ORIGIN_X,
    ORIGIN_Y,
    UNIT,
    VB_H,
    VB_W,
    project_xy,
    state_to_svg,
)


def _empty_state() -> TreeState:
    return TreeState(
        session_id="t",
        seed_hex="0" * 16,
        started_at_ms=0,
        theme="default",
    )


# ---------------------------------------------------------------------------
# Coordinate system
# ---------------------------------------------------------------------------


def test_project_xy_translates_logical_to_screen() -> None:
    assert project_xy(0, 0) == (ORIGIN_X, ORIGIN_Y)
    assert project_xy(1, 0) == (ORIGIN_X + UNIT, ORIGIN_Y)
    # Logical y up → SVG y down.
    assert project_xy(0, 1) == (ORIGIN_X, ORIGIN_Y - UNIT)


# ---------------------------------------------------------------------------
# Bare SVG structure
# ---------------------------------------------------------------------------


def test_empty_state_produces_well_formed_svg() -> None:
    svg = state_to_svg(_empty_state())
    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    assert f'viewBox="0 0 {VB_W} {VB_H}"' in svg
    # Always has sky + ground + defs.
    assert 'id="sky"' in svg
    assert 'id="ground"' in svg


def test_default_theme_emits_default_palette_colors() -> None:
    """Phase 10: the default theme uses the Anthropic warm palette.
    Ground gradient stops at PAMPAS_DEEP; trunk fills with BARK."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=y, glyph="│") for y in range(1, 5)],
    )
    svg = state_to_svg(state)
    assert tokens.PAMPAS_DEEP in svg
    assert tokens.BARK in svg


def test_python_theme_emits_bamboo_palette_colors() -> None:
    """Python → bamboo. Stalks fill with MOSS (or shaded variant);
    BAMBOO_YOUNG appears on the youngest stalk."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="python",
        trunk=[Segment(x=0, y=y, glyph="│") for y in range(1, 5)],
    )
    svg = state_to_svg(state)
    # Bamboo stalks use the moss family — at least one MOSS-derived
    # fill appears somewhere in the body.
    assert tokens.MOSS in svg or tokens.BAMBOO_YOUNG in svg


# ---------------------------------------------------------------------------
# Time-of-day sky
# ---------------------------------------------------------------------------


def test_night_hour_draws_moon_not_sun() -> None:
    svg = state_to_svg(_empty_state(), now_hour=23)
    assert 'fill="url(#moon)"' in svg


def test_day_hour_draws_sun() -> None:
    svg = state_to_svg(_empty_state(), now_hour=12)
    assert 'fill="url(#sun)"' in svg


def test_dawn_hour_uses_dawn_gradient() -> None:
    svg = state_to_svg(_empty_state(), now_hour=6)
    # Dawn token from the Anthropic palette.
    assert tokens.DAWN in svg


# ---------------------------------------------------------------------------
# Tree parts
# ---------------------------------------------------------------------------


def test_trunk_renders_as_path() -> None:
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=y, glyph="│") for y in range(1, 8)],
    )
    svg = state_to_svg(state)
    # Trunk is a filled path with BARK fill, not a stroke.
    paths = re.findall(r"<path d=\"M[^\"]+\" fill=\"#[0-9A-Fa-f]+\"", svg)
    assert len(paths) >= 1
    assert tokens.BARK in svg


def test_branches_roots_and_leaves_each_contribute() -> None:
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=y, glyph="│") for y in range(1, 6)],
        branches=[
            Branch(
                file_path="x.py", angle_deg=40, attach_point=(0, 3),
                segments=[
                    Segment(x=1, y=3, glyph="\\"),
                    Segment(x=2, y=4, glyph="\\"),
                ],
                leaves=[
                    Segment(x=2, y=5, glyph="&"),
                    Segment(x=3, y=5, glyph="*"),
                ],
                leaf_geometry_count=2,
            ),
        ],
        roots=[
            Root(
                cwd="/p", angle_deg=-110, attach_point=(0, 0),
                segments=[
                    Segment(x=-1, y=-1, glyph="/"),
                    Segment(x=-2, y=-2, glyph="/"),
                ],
            ),
        ],
        offshoots=[
            Offshoot(
                agent_id="a1", agent_type="Explore", attach_point=(0, 2),
                segments=[
                    Segment(x=-1, y=2, glyph="("),
                    Segment(x=-2, y=2, glyph="."),
                ],
            ),
        ],
        flowers=[Flower(x=2, y=6, glyph="❀", host_or_query="example.com")],
    )
    svg = state_to_svg(state)
    # Trunk + 1 branch + 1 root + 1 offshoot path = ≥4 ``<path>`` fills
    # (the actual count is higher with cluster groups and pot).
    paths = svg.count("<path")
    assert paths >= 4, f"only {paths} path elements"
    # Leaf clusters → many ellipses (two-tone count >= 5 per cluster).
    assert svg.count("<ellipse") >= 5
    # Offshoot berry circle is still drawn (the five-petal flower
    # glyph was removed in phase 10 batch 2 — only soft circles for
    # offshoot tips remain).
    assert "<circle" in svg


def test_wilted_leaf_color_is_honoured() -> None:
    """The wither pass paints leaves goldenrod; the renderer must
    forward that colour rather than overriding with palette.leaf."""
    state = TreeState(
        session_id="t", seed_hex="0" * 16, started_at_ms=0, theme="default",
        trunk=[Segment(x=0, y=1, glyph="│")],
        branches=[
            Branch(
                file_path="x.py", angle_deg=30, attach_point=(0, 1),
                segments=[Segment(x=1, y=2, glyph="\\")],
                leaves=[
                    Segment(x=2, y=3, glyph=",", color="#DAA520"),
                ],
                leaf_geometry_count=1,
            ),
        ],
    )
    svg = state_to_svg(state)
    assert '#DAA520"' in svg.upper() or "#daa520" in svg


# ---------------------------------------------------------------------------
# Real-session feed — the load-bearing visual check
# ---------------------------------------------------------------------------


def test_real_session_renders_substantive_svg() -> None:
    fixture = Path("tests/fixtures/real_session_2026-05-15.jsonl")
    events: list[tuple[int, object]] = []
    with fixture.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events.append((rec["idx"], parse_event(rec["raw"])))
    state = apply_all(
        "86abd5d6-881d-4a56-860d-fc2e2d199787",
        events,  # type: ignore[arg-type]
        theme="python",
    )
    svg = state_to_svg(state)
    # Bamboo emits stalks (4 paths) + per-event twigs / fans, so a
    # real session must produce ≥8 paths total.
    assert svg.count("<path") >= 8
    # Bamboo doesn't use ellipses for leaves (path-based teardrops);
    # the assertion is now on overall richness: the body must
    # contain leaf-fan groups (one ``<g>`` per fan).
    assert svg.count("<g>") >= 4
    # File size sanity — anything below 2 KiB is too sparse.
    assert len(svg) > 2000


# ---------------------------------------------------------------------------
# Demo tree round-trip (no exceptions)
# ---------------------------------------------------------------------------


def test_demo_tree_renders_for_every_theme() -> None:
    """Every theme must survive ``state_to_svg`` end-to-end."""
    base = demo_tree("demo")
    from dataclasses import replace

    from bonsai_cc.render.palette import PALETTES
    for theme in PALETTES:
        themed = replace(base, theme=theme)
        svg = state_to_svg(themed)
        assert svg.startswith("<svg"), f"theme {theme!r} produced bad svg"
        assert "</svg>" in svg


def test_state_to_svg_accepts_theme_override() -> None:
    """``?theme=sakura`` on a python session must render with the
    sakura body, not bamboo. The override flows through state_to_svg
    via ``theme_override`` and routes the renderer pick by display
    name (``sakura``) or language key (``swift``)."""
    from dataclasses import replace

    base = replace(demo_tree("python-proj"), theme="python")
    # Without override: bamboo (Python theme) — produces bamboo's
    # vertical stalks (no leaf-cluster ellipses on the apex).
    svg_default = state_to_svg(base)
    # With override: sakura — must produce 5-petal blossom clusters
    # (lots of small ellipses, the sakura signature).
    svg_sakura = state_to_svg(base, theme_override="sakura")
    # Sanity: the SAKURA_DEEP token only ships from the sakura
    # renderer's offshoot dot. Its presence is a strong signal that
    # the sakura renderer ran.
    assert tokens.SAKURA_DEEP in svg_sakura, (
        f"sakura override didn't activate the sakura renderer "
        f"(SAKURA_DEEP {tokens.SAKURA_DEEP} not in output)"
    )
    assert tokens.SAKURA_DEEP not in svg_default, (
        "baseline python session unexpectedly contained sakura colors"
    )
    # Both must be valid SVG.
    assert svg_default.startswith("<svg")
    assert svg_sakura.startswith("<svg")


def test_state_to_svg_unknown_theme_override_falls_back() -> None:
    """An invalid theme name must NOT crash and must not alter the
    output — silently falls back to ``state.theme``. Mirrors the
    contract documented on the picker: ``?theme=garbage`` in the
    URL won't break the daemon."""
    from dataclasses import replace

    base = replace(demo_tree("python-proj"), theme="python")
    svg_baseline = state_to_svg(base)
    svg_fallback = state_to_svg(base, theme_override="bogus-theme-name")
    assert svg_fallback == svg_baseline, (
        "unknown theme override should fall back to state.theme "
        "byte-for-byte, not partially override / crash / change colors"
    )


def test_state_to_svg_theme_override_accepts_language_key() -> None:
    """``?theme=swift`` (language key) is equivalent to
    ``?theme=sakura`` (display name) — both route to the same
    renderer. The mapping covers both for robustness so a URL
    written from server-side knowledge of language keys still
    works."""
    from dataclasses import replace

    base = replace(demo_tree("python-proj"), theme="python")
    svg_display = state_to_svg(base, theme_override="sakura")
    svg_language = state_to_svg(base, theme_override="swift")
    assert svg_display == svg_language, (
        "language key and display name must produce identical output"
    )


def test_density_level_tiers() -> None:
    """``density_level`` caps at 3 so a 500-event session doesn't push
    the renderer past its visual budget."""
    from bonsai_cc.web.render.canvas import density_level

    assert density_level(0) == 0
    assert density_level(20) == 0      # threshold inclusive of baseline
    assert density_level(21) == 1
    assert density_level(50) == 1
    assert density_level(51) == 2
    assert density_level(100) == 2
    assert density_level(101) == 3
    assert density_level(500) == 3     # cap holds at very long sessions
    assert density_level(10_000) == 3


def test_bamboo_grows_extra_stalks_at_high_event_count() -> None:
    """The user-visible symptom that motivated the fix: bamboo
    stalls visually around event 20 because ``_STALK_COUNT`` is
    fixed at 4. The renderer now adds up to 4 thinner peripheral
    stalks as density rises — caps at 8 total. The proxy here is
    SVG ``<path>`` count, which grows with each new stalk +
    its node markers + apex fan.
    """
    from dataclasses import replace

    from bonsai_cc.web.render import canvas
    from bonsai_cc.web.render.bamboo import render_bamboo

    base = replace(demo_tree("bamboo"), theme="python")
    ctx = canvas.CanvasCtx(hour=12)
    svg_low = render_bamboo(replace(base, event_count=5), ctx)
    svg_high = render_bamboo(replace(base, event_count=150), ctx)
    paths_low = svg_low.count("<path")
    paths_high = svg_high.count("<path")
    # Each extra stalk contributes ~6 <path>s (ribbon + shade line +
    # node ticks). At density_level=3 we expect 4 extra stalks → ~24
    # additional paths. Lower bound at 12 keeps the assertion robust
    # to per-render jitter while still catching a regression that
    # silently drops the extra-stalks branch.
    assert paths_high - paths_low >= 12, (
        f"bamboo at event_count=150 added only "
        f"{paths_high - paths_low} <path>s vs event_count=5 "
        f"(expected ≥12 from extra peripheral stalks)"
    )


def test_every_theme_survives_long_session() -> None:
    """Render-the-tree-doesn't-crash smoke at the density cap.

    Event count 500 is well past the level-3 threshold (100). If
    any renderer's enrichment math overflows / produces malformed
    SVG / unbounded loops, this test catches it.
    """
    from dataclasses import replace

    from bonsai_cc.render.palette import PALETTES
    base = demo_tree("long-session")
    long_state = replace(base, event_count=500)
    for theme in PALETTES:
        themed = replace(long_state, theme=theme)
        svg = state_to_svg(themed)
        assert svg.startswith("<svg"), f"theme {theme!r} bad svg at 500 events"
        assert "</svg>" in svg
        # SVG should grow at level 3, not stay frozen at baseline.
        baseline = state_to_svg(replace(themed, event_count=5))
        assert len(svg) >= len(baseline), (
            f"theme {theme!r}: enriched SVG ({len(svg)}B) is smaller "
            f"than baseline ({len(baseline)}B) — enrichment regression"
        )
