# Testing Paddock

Everything below runs on the **Atlas dev build** — not production. Break it freely.

| | |
|---|---|
| **Fan Zone** (public, no login) | http://atlas.paddock/paddock |
| **Team workspace** (login) | http://atlas.paddock/racing |
| **PLM app** | http://atlas.paddock/app |

> `atlas.hyperplm` is **not** this build — another session repointed it at a frozen
> mirror of what's live on hyperplm.com. `hyperplm.atlas` also works if you prefer it.

If a page looks stale, hard-reload (**Ctrl+Shift+R**). If the hostname won't resolve,
`ipconfig /flushdns` on Windows.

---

## The 10-minute pass

The most useful thing you can do is walk the path a real customer walks.

### 1. Start as a brand-new team
Go to **/racing**, sign out if you're signed in, then register a fresh account at
`/login` (self-registration is on). You get your own organisation — completely
isolated from any other team's data.

You should land on an **empty** workspace that tells you what to do rather than
showing a broken-looking grid.

1. **Create a team** — the empty state has the button. Name, car number, series, class.
2. **Car & Build** → add a car and a driver.
3. **Lap Times** → `+ Session` → create a new event (pick a circuit from the 23) →
   choose FP1.
4. `+ Log lap` → enter a time as `1:41.208`. Log two or three.
   *Check:* the fastest is automatically marked purple; the chart draws itself.
5. **Setup Sheets** → `+ New sheet` → add a few values → **Clone as revision**.
   *Check:* the clone carries the values across.
6. **Checklists** → create one with a few lines → click items to sign off.
   *Check:* it records who and when.
7. **Parts & CAD** → create a part in the PLM app first, then `+ Track a part`
   with an hours limit. Click the row to log hours.
   *Check:* status flips ok → service_soon at 80% → over at 100%. That's computed
   by the database; you cannot set it by hand.

### 2. Publish, and see what a fan sees
Nothing is public until you say so.

1. Top bar → the **○ Private** pill → publish the team.
2. **Lap Times** → publish a session. Its laps go public with it.
3. Open **/paddock** in a private window (so you're anonymous).
   *Check:* your team appears, with the laps you published — and **no setup sheet**.
4. Back in the workspace, publish a setup sheet. Refresh the fan page.
   *Check:* now it appears. Unpublish it and it disappears again.

**This is the thing to try hardest to break.** A fan should never see a setup
sheet, a checklist, a part, or an unpublished session.

### 3. Be a fan
On **/paddock** → **Join as a fan**. You get an account with **no team** — a
spectator.

- Follow a team. First follow: **+50 points and the First Follow badge**.
- **My Paddock** shows points, badges, who you follow and a points ledger.
- Try visiting **/racing** as this fan.
  *Check:* it explains you're a fan and points you back, rather than erroring.

### 4. Predictions
As the team, in **Lap Times** → **Fan questions** → `+ Ask`. Pose a question on a
session with two or more options and a points value.

- As the fan, **Predict** tab → make a pick. Change it — allowed while open.
- As the team, **Lock** it. The fan can no longer pick.
- **Resolve** with the correct answer.
  *Check:* only correct fans are paid; the ledger says why; hitting Resolve twice
  pays nothing the second time.

---

## Things I'd like you to try to break

- Sign up a **second team** in another browser. Confirm you cannot see the first
  team's anything — teams, laps, setups, parts, questions.
- Guess an id: `/api/racing/teams/1/laps` while signed in as the other org.
- Follow an **unpublished** team by URL.
- Resolve the same question twice quickly.
- Log a lap time in a strange format (`95.4`, `1:41`, `abc`).
- Create a team with no car number, or a checklist with no items.

## Known gaps (not bugs)

- **No delete anywhere.** You can create and edit, not remove. Wipe with the reset below.
- Fan sign-in and team sign-in are separate forms; a team member who wants to
  follow teams gets a fan profile automatically on first follow.
- The Welcome/marketing framing is minimal — the Fan Zone opens straight onto teams.
- Points are only earned from follows and predictions. Streaks aren't built.

## Reset the dev data

Wipes every team, lap, fan and prediction on the **dev** database only:

```bash
docker exec -e PGPASSWORD=<pw> hyperplm-test-db psql -U owner -d hyperplm_test \
  -c "TRUNCATE organizations, users RESTART IDENTITY CASCADE"
```

Circuits and badges are seeded by migration and survive. Then register again from
scratch — that is the cold-start path, and it's covered by `tests/test_cold_start.py`.

## Running the tests

```bash
cd /home/kkers/projects/HYPERPLM
ALEMBIC_DATABASE_URL=... DATABASE_URL=... SECRET_KEY=... \
  .venv-test/bin/python -m pytest tests/ -q
```

63 tests: tenant isolation, the fan wall, fan accounts, predictions, the UI
delivery contract, and the cold-start journey.
