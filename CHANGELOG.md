# Changelog

All notable changes to HYPERPLM are documented here.

Versioning: `MM.mmm.ppp` (major . version . patch). The first working release is
`00.001.000`; everything built before it ships *as* `00.001.000` (the number does not
increment while building the first version). Post-release fixes bump the last group
(`00.001.001`…); the next feature release bumps the middle group (`00.002.000`).

## 00.001.000 — first version (IN DEVELOPMENT)

The first working release. Everything below is part of building `00.001.000`; the version
does not increment until it ships. After release, fixes will be `00.001.001`, `00.001.002`,
…, and the next feature version will be `00.002.000`.

### Baseline & branding
- Import baseline codebase: PLM Lite V1.0 from the live 3dprintdudes.io/plm deployment
  (VPS1 /opt/plm, commit 46f5e36 + 2 untracked files) — FastAPI + vanilla JS: parts,
  BOM/relationships, documents, auth (local/Google OAuth), check-in/out, release status,
  Excel export. Excluded .env, deploy.sh, .git, __pycache__.
- Proprietary LICENSE, All Rights Reserved (Joshua M. Grace); relicensed from the prior
  MIT "PLM Lite" by the same owner. README/login footer de-MIT'd; renamed to HYPERPLM.
- Coming-soon landing page (landing/index.html), motorsports theme (racing red/amber,
  race-team copy). Live at https://hyperplm.com (nginx + Let's Encrypt).
- CLAUDE.md: Rule 0 (never put secrets anywhere public), Deployment section (Docker,
  contractor VPS, 127.0.0.1:4000 behind nginx). Infra IPs kept out of the repo.

### Security hardening
- `config.validate()` fail-fast: refuses production startup on unset/default/short
  SECRET_KEY (JWTs forgeable otherwise; repo is public). Warns in development.
- No default admin in production: removed hardcoded admin/admin123; first admin comes from
  BOOTSTRAP_ADMIN_USERNAME/PASSWORD (forced password change on first login).
- Login rate limiting (app/security.py, 10/5min/IP, X-Forwarded-For aware) on the auth
  routes; security headers middleware; path-traversal guard hardened (Path.is_relative_to);
  centralized password policy (PASSWORD_MIN_LENGTH).
- Independent review by user — PASSED. Non-blocking follow-ups filed in
  docs/phase1_review_followups.md (fold in during the tenancy work).

### Multi-tenancy + PostgreSQL
- Design: docs/phase2_design.md — org/membership model, two-layer isolation (app scoping +
  PostgreSQL RLS backstop, which structurally closes the IDOR class), SQLite→Postgres
  migration. Decisions: SQLAlchemy Core, Docker Postgres on the contractor VPS,
  multi-org-per-user, per-org custom roles. Independent design review — PASSED (§12
  follow-ups). Infra IPs scrubbed from the public repo (HEAD; no history rewrite).
- PostgreSQL data layer: app/db.py (SQLAlchemy Core metadata) + Alembic (migrations/0001
  initial schema) + deploy/docker-compose.yml (Postgres 16 localhost-only + app). Live on
  VPS at rev 0001. Independent review by user — PASSED.
- Tenancy + row-level security: migrations/0002 (hand-authored) — organizations +
  org_members, role moved to per-membership, per-org roles, org_id on all 8 tenant tables,
  ENABLE + FORCE ROW LEVEL SECURITY + isolation policies keyed on the app.current_org GUC
  (bare current_setting = fail closed loud), non-superuser hyperplm_app role. Live isolation
  suite PASSED (rev 0002). is_platform_admin flag added; bypass path NOT wired.
- Tenant-scoped connection layer: app/tenancy.py — tenant_session (SET LOCAL, txn-scoped,
  no pool leak) / global_session (no GUC, global tables only) / TenantDB; membership on the
  global path, role abilities under tenant context. Split DB URLs (owner for migrations,
  hyperplm_app for the app). Live-validated as the app role.
- Data-access layer: app/repo.py — SQLAlchemy Core queries porting the SQLite database.py.
  Tenant ops (parts, attributes, revisions, relationships/BOM, documents, file versions,
  audit, per-org roles) take a TenantDB and rely on RLS for scoping; INSERTs set org_id.
  Global ops (users, orgs, members) take a global_session connection. Additive — the app
  still runs on SQLite database.py until the routers switch over. Fixed a recursive-BOM CTE
  type mismatch surfaced by Postgres's strict typing (cast the anchor part_id to BIGINT).
  Live-validated on VPS Postgres: full parts/BOM/attribute/revision/audit flow works and
  cross-org isolation holds (other org sees 0 rows, BOM empty, cross-org get returns None,
  same part_number reusable across orgs).
- App switchover to Postgres (the request layer now runs on RLS, not SQLite):
  - app/deps.py — per-request principal resolution (user + active-org membership re-read
    every request on the global path; JWT active_org_id is only a hint, §12.1) and a single
    tenant_session per request (RequestContext) with role abilities; require_ability/require_admin.
  - app/auth.py rewritten to pure JWT + password + Google helpers (no DB); app/accounts.py
    service layer (register-creates-org, seed default roles, Owner membership, Google/Windows
    first-login provisioning, default-org resolution).
  - Routers ported onto repo + RequestContext: parts, relationships, documents (files.py
    refactored to TenantDB), users (now org-membership management), admin (per-org roles/
    audit/attribute keys). New orgs_router: list/create/switch active org. auth_router adds
    /auth/register and /auth/switch flow; /me returns active-org abilities.
  - main.py: lifespan startup (config validate + DB ping; no SQLite init), security headers,
    orgs_router wired. Retired the SQLite era: removed database.py, schema.sql, permissions.py,
    and the stale duplicate app/auth_router.py + app/index.html. Dockerfile ships migrations.
- Reviews: the PostgreSQL data layer, the tenancy migration (0002) + RLS, and the
  tenant-scoped connection layer (app/tenancy.py) were independently reviewed by the user
  — all APPROVED (2026-07-27). The router switchover (deps/auth/accounts/orgs + ports) is
  PENDING review.
- Security follow-ups folded in (from docs/phase1_review_followups.md): rate limiter now uses
  FAILURE-ONLY counting for login/windows (a guard dependency checks without recording; the
  handler records only on rejected creds) so successful logins can't cause a lockout;
  registration counts all attempts (separate 10/hour limiter). X-Forwarded-For honored only
  when TRUST_PROXY is set (default off) to prevent spoofed rate-limit evasion. Expired
  limiter keys evicted lazily + key-count sweep (no unbounded growth). Removed the redundant
  path-guard check in files.get_file_path. (Lifespan migration and no-default-admin were
  already handled in step 5; bootstrap-password validation is moot — registration replaced
  admin-seeding.)
- Tenant-isolation acceptance suite (tests/, pytest) — Phase 2 §8 gate. 12 tests, all green
  against live Postgres: parts/BOM/relationships/documents/revisions/audit isolation via API
  AND direct-id (IDOR) access; part-number unique per org; org switch changes the dataset and
  non-members can't switch; Viewer role can't write; unauthenticated → 401; DB-layer RLS fails
  closed (no-GUC query errors, global_session can't read tenant tables); and a single-org
  regression covering the full parts→BOM→checkout→release→revise→export flow. conftest resets
  the in-process rate limiter + truncates between tests. requirements-dev.txt + pytest.ini added.

**Phase 2 (multi-tenancy + PostgreSQL) is functionally complete** — the app runs isolated on
Postgres with a passing acceptance suite. Remaining before first release: deploy the app
container on port 4000 behind nginx (hyperplm.com), and a final independent review of steps 4-7.

### Racing domain (Phase 3, step 1) — schema

- Migration `0003_racing_domain.py`: the motorsports domain built **on** the PLM foundation
  rather than beside it. A company IS an `organizations` row; team membership and roles ARE
  `org_members` + `roles`; a part IS a `parts` row. No parallel identity, permission, or
  parts system was introduced.
- Tenant tables (org_id + RLS ENABLE/FORCE + `<table>_tenant_isolation` policy, identical
  contract to 0002): `teams`, `cars`, `drivers`, `events`, `run_sessions`, `laps`, `setups`,
  `setup_values`, `checklists`, `checklist_items`, `part_usages`.
- Global tables (no RLS — resolved with no active org, by anonymous or cross-org callers,
  same rationale as `users`/`org_members`): `tracks` and `badges` (reference data, seeded),
  plus the fan layer `fan_profiles`, `follows`, `predictions`, `point_ledger`, `user_badges`,
  `garage_passes` — a fan belongs to a user across orgs, not to one tenant.
- `part_usages` adds only the racing context a PLM part lacks: which car runs it and its
  service life (hours/cycles vs limit → ok | service_soon | over), preserving the
  CAD-revision → part → setup → lap-time thread inside one database.
- Public vs private is a **column, not a second store**: `visibility` ('public' | 'team') on
  `run_sessions`, `laps` and `setups`. Setups default to `'team'` (crown-jewel IP);
  sessions/laps default to `'public'`. Serving a public team page still scopes to exactly one
  tenant — the app resolves team → org_id, sets the GUC to that org, and additionally filters
  `visibility='public'` for anonymous viewers. RLS keeps cross-tenant isolation; the
  visibility column keeps the fan wall. **Flagged for review:** the fan wall is app+column
  level by design; RLS was never the mechanism for it.
- Seeded reference data: 23 IMSA/IndyCar circuits and 6 fan badges.
- `app/db.py` (SQLAlchemy Core source of truth) extended with the same tables and
  `TENANT_TABLES` widened to include the 11 racing tenant tables.
- Verified on a disposable Postgres 16 copy on Atlas (never the live DB): `alembic upgrade
  head` clean from scratch; 31 tables, 19 RLS policies, 11 racing tables FORCE RLS; isolation
  proven as the non-superuser app role — no-GUC query ERRORS (fails closed), each org sees
  only its own rows, cross-tenant INSERT rejected by WITH CHECK; `downgrade 0003 → 0002`
  removes the domain cleanly and re-upgrade round-trips.
- **Not yet reviewed** (rule 5): author session does not approve its own work. Pending an
  independent check before this is released as the new version.

### Racing domain (Phase 3, step 2) — API + workspace UI

- `app/racing.py`: racing query module (rule 3 — one module, one responsibility). Takes the
  request's TenantDB, so RLS scopes every statement; org_id is never passed around. The fan
  wall lives here in one place: `public_only=True` restricts a query to published rows.
- `app/routers/racing_router.py`: `/api/racing/{summary,tracks,teams,teams/{id}/laps,
  teams/{id}/sessions,setups,setups/{key},checklists,parts}` — same
  `Depends(require_ability(...))` pattern as the parts/documents routers.
  A team-only sheet requested with `public_only=true` returns **404, not 403**, so a fan
  learns nothing about the existence of a private sheet.
- `static/racing.html` + `GET /racing`: the Paddock workspace — overview KPIs, the 23-circuit
  track library, session log and lap times (timing-tower colours: purple = fastest, green =
  PB), setup sheets with a click-through detail pane, checklists, and life-limited parts with
  service bars. Reads live from the API; no hardcoded data.
- `main.py` stays thin (rule 3): one router include and one page route.
- Verified end-to-end on the Atlas testing copy (http://atlas.hyperplm): login → 23 circuits
  served from the seeded reference data, 3 teams, 4 laps with 1:41.208 flagged fastest,
  the 14-value WG-2025-Q sheet readable by the engineer, and the SAME sheet returning 404
  with `public_only=true` — the fan wall holding through the HTTP layer.
- Local testing copy documented in `R:\port_mapping.txt` (Atlas :4100, nginx vhost
  `atlas-hyperplm`, dnsmasq `atlas.hyperplm`). Production VPS remains untouched at rev 0002.
- **Not yet reviewed** (rule 5).

## 00.000.001 — 2026-07-21

- Add .gitignore: excludes PAT/token files, keys, .env, and Python artifacts (repo is public).

## 00.000.000 — 2026-07-21

- Initial repository setup: CLAUDE.md project rules, VERSION file, CHANGELOG.md (no code yet).

### Racing domain (Phase 3, step 3) — fan layer + navigation

- Migration `0004_public_team_directory.py`: `team_directory` (global, no RLS) — the minimal
  public identity of a team plus its org_id. Fans are anonymous and have no active org, so
  they cannot read `teams` (RLS fails closed); this is the lookup that makes a public page
  possible. Flow: directory → org_id → `tenant_session(org_id)` → rows WHERE
  visibility='public'. RLS still isolates tenants; the visibility column still walls the fans.
  Teams appear publicly only when `is_public` is set.
- `app/routers/public_router.py`: the app's only unauthenticated data path —
  `/api/public/{teams,tracks,teams/{slug},teams/{slug}/setups,leaderboard}`. Nothing accepts
  a caller-supplied org_id; team-only setups can never appear.
- `static/paddock.html` + `GET /paddock`: the public Fan Zone — browse published teams, open
  a team for its published lap times and session log, the 23-circuit library, the fan
  leaderboard, and an explicit "what fans get / what never leaves the garage" breakdown.
- Navigation fix: the racing workspace and Fan Zone were unreachable from the PLM UI (a real
  usability bug — the work existed but was invisible). Added menu + sidebar entries in
  `static/app.html` and retitled the shell from "PLM Lite v1.0" to "HYPERPLM Paddock".
- Verified unauthenticated on the Atlas testing copy: 3 published teams, #74 returning both
  drivers, 4 public laps, best 1:41.208 — and 0 public setup sheets, because WG-2025-Q is
  team-only. The fan wall holds on the anonymous path.
- **Not yet reviewed** (rule 5).

### Racing domain (Phase 3, step 4) — the designed UI on the real database

- Replaced the utilitarian racing/fan pages with the approved Paddock design
  (`static/racing.html`, `static/paddock.html`) — the ember/graphite motorsports system:
  timing-tower colours, mono tabular data, mini circuit maps, light/dark, the Team ⇄ Fan
  visibility toggle, and the full public set (Welcome / Company / Team / Profile / Fan).
- The designs are hydrated, not rewritten: an appended script swaps the mock content for
  live rows from `/api/racing/*` (authenticated) and `/api/public/*` (anonymous), keeping
  the markup and CSS untouched. Circuit cards reuse the design's own mini-map paths, matched
  to the seeded track list by name.
- Mock CTAs now point at the real app (`/login`, `/racing`).
- **Not yet reviewed** (rule 5).

### Racing domain (Phase 3, step 5) — writes + database integrity

- Migration `0005_racing_integrity.py` — two rules moved into the database so they hold
  regardless of which code path writes:
  - `sync_team_directory()` trigger mirrors `teams` into `team_directory` on INSERT/UPDATE
    (0004 only backfilled, so a team created afterwards would have been invisible to fans).
    Slugs are derived and de-duplicated; `is_public` is preserved across updates so a rename
    never silently republishes. New teams default to **not public** — publishing is opt-in.
  - `derive_part_status()` trigger computes `part_usages.status` from hours/cycles against
    their limits (>=100% over, >=80% service_soon, else ok), so a caller can no longer store
    a status that contradicts the numbers.
- Write layer in `app/racing.py`: teams, cars, drivers, events, sessions, laps, setups +
  values, setup cloning (how a revision is actually made), checklists, item sign-off, part
  hours, and team publish. Every insert sets org_id from the tenant session, so RLS
  WITH CHECK rejects a cross-tenant write at the database.
- `app/routers/racing_write_router.py` (separate from the read router — different gating):
  reads need `view`, writes need `write`, and **publishing needs `release`** — pushing data
  to the fan side is as deliberate an act as releasing a part.
- Logging a lap recomputes PB/fastest for that session automatically; publishing a session
  publishes its laps with it.
- Verified end-to-end on the Atlas copy: created a team (correctly NOT public), published it
  (appeared to fans), ran event → session → 3 laps with the fastest auto-flagged, and updated
  part hours to watch the trigger re-derive the status.
- **Not yet reviewed** (rule 5).

### Racing domain (Phase 3, step 6) — operating controls (issues #3, #4)

- Write actions in the racing workspace, added to the approved design without touching its
  markup (a second appended script, same pattern as the hydration layer):
  - **Log lap** — pick a session, enter `1:41.208`, choose tire. PB/fastest are recomputed
    server-side for the session.
  - **New session** — against an existing event or creating one (track picker from the
    seeded circuit list).
  - **Checklist sign-off** — click an item; the API records who and when.
  - **Log part hours** — click a parts row; `status` is re-derived by the 0005 trigger.
- Publish controls (the fan wall, now operable):
  - Team **Public / Private** pill in the context bar.
  - Per-session **Publish / Unpublish** in the session table — publishing a session publishes
    its laps with it.
  - **Publish sheet** on the setup page, behind a confirm, since it exposes corner weights,
    dampers and aero.
  - All publish paths require the `release` ability, not just `write`.
- Read API now exposes `checklist_items.id` and `run_sessions.event_id`, which the controls
  need to target rows.
- Verified on the Atlas copy: signed off an item (4/6 → 5/6); un-published a session and
  watched the fan view drop from 4 laps to 3, then republished and saw it return to 4.
- **Not yet reviewed** (rule 5).

### Racing domain (Phase 3, step 7) — fixes to #3/#4 found by driving the real UI

Reported as "#3 and #4 don't work". Verified in a real browser against
atlas.hyperplm; four separate causes, none of which the API-level tests could catch:

- **Modal froze the page.** The overlay used `backdrop-filter: blur()` across the full
  viewport, composited over the design's large inline SVG charts. Painting stalled hard
  enough that clicking a control looked like nothing happened. Replaced with a plain scrim.
- **Stale HTML served from browser cache.** `/racing` and `/paddock` are static shells that
  change every deploy, so a cached copy silently hid the new controls entirely. App pages
  now send `Cache-Control: no-store, must-revalidate`.
- **Workspace defaulted to a team with no data.** It took `teams[0]`, which sorts by car
  number — that was #07, a team with zero sessions and zero laps, so every panel looked
  empty and "＋ Log lap" refused to open. It now defaults to the team that has actually run,
  and a **team switcher** in the context bar lets you change it (remembered per browser).
- **Blocking native dialogs.** `alert()`/`confirm()` froze the renderer under the extension
  and are hostile in an operating tool. Replaced with in-page notice/confirm modals.

Also hydrated three panels that were still showing mock content: the season/weekend charts
(now real lap times, session-fastest in purple), "This weekend" (now the real checklist with
its sign-off count), and Fan Reach (now the true published state, not a fabricated follower
count).

Verified end-to-end in the browser: logged FP1 lap 7 at 1:39.512 on Soft through the modal
and confirmed it persisted and was flagged fastest for its session.

### Racing domain (Phase 3, step 8) — acceptance tests (issue #8)

- `tests/test_racing_isolation.py` (11 tests) mirrors the Phase 2 suite for the racing
  domain, testing the two guarantees separately because they are different mechanisms:
  **RLS keeps orgs apart** (cross-org reads return nothing; direct-id/IDOR access 404s;
  you cannot publish another org's team) and **`visibility` keeps fans out** (a new team is
  private until published; a team-only setup is invisible to fans and returns 404 rather
  than 403 so its existence stays hidden; unpublishing a session withdraws its laps and
  republishing restores them; unpublishing a team makes its slug unreachable).
  Also covers publish gating (a Viewer can read but cannot write or publish) and the 0005
  database rules (part status is derived from hours vs limit, not trusted from the caller;
  renaming a team does not republish it).
- `tests/test_ui_contract.py` (13 tests) exists because the controls passed every API test
  and were still unusable in a browser. It pins each cause as a cheap HTTP/static
  assertion — no browser needed, which is the point: a suite that only exercises JSON
  endpoints cannot see any of them. Page shells must send `no-store`; no `alert`/`confirm`
  in shipped UI; no **full-viewport** backdrop-filter (blurring a small sticky bar is fine
  and stays allowed — only the whole-viewport case is flagged); the write/publish controls
  and team switcher must be present in the markup; public pages must work anonymously while
  the racing API still 401s.
- Verified by mutation: reintroducing the three regressions (full-screen blur, `alert()`,
  and dropping `no-store`) fails 6 tests; reverting turns them green again. A test that
  cannot fail on the bug it describes is worthless, so this was checked rather than assumed.
- Suite run against a dedicated `hyperplm_pytest` database (the demo instance on the Atlas
  testing copy is left intact): **35 passed**.

### Racing domain (Phase 3, step 9) — fan accounts (issue #6)

- `app/fans.py` + `app/routers/fans_router.py`: registration, sign-in, follow/unfollow,
  points and badges for **fans** — accounts that belong to no organization. This is the
  other half of the association model: a member has an `org_members` row and reaches the app
  through the tenant path; a fan has a `fan_profiles` row and no membership, so that path
  refuses them by design (`get_principal` 403s with "No organization for this account").
  Fans therefore use their own `current_fan` dependency, which resolves the session **without**
  touching membership.
- That is also the security boundary: nothing in the fan router opens a tenant session or
  reads a tenant table. Fans touch only global tables (`fan_profiles`, `follows`,
  `point_ledger`, `badges`, `team_directory`); published team data is read through the
  anonymous `/api/public/*` routes, which do the directory → tenant_session →
  `visibility='public'` resolution in one audited place.
- Registration deliberately does **not** reuse `accounts.register_local_user`, which
  provisions an org — that would make every fan a tenant and erase the distinction.
- A person can be both: a member who follows a rival team gets a profile with
  `fan_type='member'` and keeps their org access. Following a private team is refused
  (404) so an unpublished team cannot be discovered by slug.
- First follow awards 50 points and the *First Follow* badge; later follows award 10. Points
  are an append-only ledger, so unfollowing does not claw them back. Following twice is
  idempotent and does not double-award.
- Fan Zone UI wired to the real API: Sign in / Join as a fan in the header, live follow
  buttons on every team card and on the team page, and a signed-in summary (teams followed,
  points, badges).
- `tests/test_fans.py` (11 tests) covers the boundary — a fan is 403 on every tenant route,
  member signup still provisions an org, follow/unfollow, private teams unfollowable,
  anonymous 401, points/badges, idempotency, leaderboard, and the member-who-is-also-a-fan.
- Two bugs found by driving the real browser rather than the API: the fan controls targeted
  the wrong DOM (the shipped page is the full Paddock design, not the earlier simple shell),
  and a `MutationObserver` repaint re-entered itself forever and wedged the renderer. Both
  fixed; the observer is now coalesced through `requestAnimationFrame` with a re-entrancy
  guard, and `tests/test_ui_contract.py` gained a check for exactly that pattern.
- Suite: **48 passed**.

