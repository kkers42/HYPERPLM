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
    body = client.get("/racing").text
    for needle, why in [
        ("Log lap", "write action: log a lap"),
        ("＋ Session", "write action: create a session"),
        ("Publish sheet", "publish control: setup visibility"),
        ("pk-team", "team switcher (workspace defaulted to an empty team without it)"),
        ("pickTeam", "default to the team that has actually run"),
    ]:
        assert needle in body, f"/racing is missing {why!r}"


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
