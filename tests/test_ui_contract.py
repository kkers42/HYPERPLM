"""
HYPERPLM — UI delivery contract (Phase 3, issue #8).

These exist because the racing controls shipped "working" by every API test and
were still unusable in a browser. Each test here pins one of the four causes:

  1. a full-viewport backdrop-filter over the inline SVG charts stalled painting,
     so opening a modal looked like a hang;
  2. the page shells were cached by the browser, hiding new controls entirely;
  3. blocking native dialogs (alert/confirm) froze the renderer;
  4. the workspace defaulted to a team with no data, so every panel looked empty.

They are cheap HTTP/static assertions — no browser required — which is the point:
a suite that only exercises JSON endpoints cannot see any of this.
"""
import re

import pytest

PAGES = ["/login", "/app", "/racing", "/paddock"]


@pytest.fixture
def client(make_client):
    return make_client()


# ── 2. page shells must never be cached ───────────────────────────────────────

@pytest.mark.parametrize("path", PAGES)
def test_app_pages_are_not_cacheable(client, path):
    r = client.get(path)
    assert r.status_code == 200, path
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc, (
        f"{path} may be cached; a stale shell hides newly deployed controls "
        f"(got Cache-Control: {cc!r})")


# ── 3. no blocking native dialogs in shipped UI ───────────────────────────────

@pytest.mark.parametrize("path", ["/racing", "/paddock", "/app"])
def test_no_blocking_dialogs(client, path):
    body = client.get(path).text
    hits = re.findall(r"\b(alert|confirm|prompt)\s*\(", body)
    assert not hits, (
        f"{path} uses blocking dialog(s) {set(hits)}; they freeze the renderer — "
        "use the in-page notice/confirm modal instead")


# ── 1. no full-viewport backdrop-filter (paint killer over the SVG charts) ────

@pytest.mark.parametrize("path", ["/racing", "/paddock"])
def test_no_backdrop_filter_on_fullscreen_overlays(client, path):
    """Blurring a small sticky bar is fine; blurring the whole viewport on top of
    the inline SVG charts is what stalled painting. Flag only the latter."""
    body = client.get(path).text
    offenders = []
    for rule in body.split("}"):
        flat = rule.replace(" ", "").lower()
        if "backdrop-filter" in flat and ("inset:0" in flat or "top:0;left:0;right:0;bottom:0" in flat):
            offenders.append(rule.strip()[:90])
    assert not offenders, (
        f"{path} blurs a full-viewport layer: {offenders}. Composited over the "
        "SVG charts this stalls painting badly enough to look like a hang.")


# ── 4. the operating controls are actually present in the shipped markup ─────

def test_racing_page_ships_its_controls(client):
    """Assert on the endpoints the controls actually call, not on button labels —
    labels are cosmetic and change; a missing endpoint is a missing feature."""
    body = client.get("/racing").text
    for needle, why in [
        ('"/api/racing/laps"', "log a lap"),
        ('"/api/racing/sessions"', "create a session"),
        ('"/api/racing/setups"', "create a setup sheet"),
        ("/visibility", "publish / unpublish controls"),
        ("/public", "publish the team to the Fan Zone"),
        ("pk-sel", "team switcher"),
        ("localStorage", "remembering the selected team"),
    ]:
        assert needle in body, f"/racing is missing {why!r}"


def test_racing_page_has_no_hardcoded_content(client):
    """The page must render from the API. Literal counts and invented figures are
    how it ended up looking like a mockup with a few live gauges."""
    body = client.get("/racing").text
    import re as _re
    counts = _re.findall(r'<span class="cnt">\s*\d+\s*</span>', body)
    assert not counts, f"hardcoded sidebar counts in the markup: {counts}"
    for fake in ["1,847", "Crew on the timing stand", "Michelin · slick",
                 "cross 50.4%", "Zanardi"]:
        assert fake not in body, f"invented content still in /racing: {fake!r}"


def test_racing_page_has_empty_states(client):
    """Every panel must say what to do when it has no data — an empty grid reads
    as broken."""
    body = client.get("/racing").text
    assert "empty" in body
    for needle in ["No sessions yet", "No setup sheets", "No parts tracked", "No team yet"]:
        assert needle in body, f"/racing has no empty state for {needle!r}"


def test_paddock_page_ships_the_public_shell(client):
    body = client.get("/paddock").text
    assert "/api/public/teams" in body, "fan page must read the public API"
    assert "api/racing/" not in body, (
        "the public fan page must not call authenticated racing endpoints")


# ── the fan page must work with no session at all ────────────────────────────

def test_public_pages_need_no_login(client):
    for path in ["/paddock", "/api/public/teams", "/api/public/tracks",
                 "/api/public/leaderboard"]:
        assert client.get(path).status_code == 200, f"{path} must be anonymous"


def test_private_api_still_requires_login(client):
    for path in ["/api/racing/teams", "/api/racing/setups", "/api/racing/parts"]:
        assert client.get(path).status_code == 401, f"{path} must require auth"


# ── the fan layer must be wired to the fan API, and only the fan API ─────────

def test_paddock_ships_fan_account_controls(client):
    body = client.get("/paddock").text
    for needle, why in [
        ("/api/fan/register", "fan signup"),
        ("/api/fan/follow/", "follow / unfollow"),
        ("/api/fan/me", "the fan's own profile"),
        ("toggleFollow", "a follow control bound to a team slug"),
    ]:
        assert needle in body, f"/paddock is missing {why!r}"


# ── a MutationObserver that repaints must not be able to re-enter ────────────

def test_mutation_observers_are_guarded(client):
    """An observer on body+subtree whose callback writes to the DOM will retrigger
    itself forever and wedge the renderer — which is exactly what happened while
    wiring the follow buttons. Require a coalescing or re-entrancy guard."""
    for path in ["/racing", "/paddock"]:
        body = client.get(path).text
        if "MutationObserver" not in body:
            continue
        assert ("requestAnimationFrame" in body or "painting" in body), (
            f"{path} uses MutationObserver with no coalescing/re-entrancy guard; "
            "a repaint inside the callback re-triggers the observer forever")


def test_paddock_has_no_invented_content(client):
    """The Fan Zone must show real published teams — not demo teams that do not
    exist in the database."""
    body = client.get("/paddock").text
    for fake in ["Apex GT", "Northline", "Vanguard Motorsport", "Cardinal Racing",
                 "1,847", "3,900"]:
        assert fake not in body, f"invented content still in /paddock: {fake!r}"


def test_paddock_has_empty_states(client):
    body = client.get("/paddock").text
    for needle in ["No teams have published yet", "No published laps yet",
                   "Join the Paddock"]:
        assert needle in body, f"/paddock has no empty state for {needle!r}"


def test_public_board_and_stats_are_anonymous(client):
    """The landing figures must come from the database, with no session."""
    for path in ["/api/public/laps", "/api/public/stats"]:
        r = client.get(path)
        assert r.status_code == 200, f"{path} must be anonymous"
    stats = client.get("/api/public/stats").json()
    for key in ["published_teams", "circuits", "fans", "follows"]:
        assert key in stats, f"/api/public/stats missing {key}"

