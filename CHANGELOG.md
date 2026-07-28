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

## 00.000.001 — 2026-07-21

- Add .gitignore: excludes PAT/token files, keys, .env, and Python artifacts (repo is public).

## 00.000.000 — 2026-07-21

- Initial repository setup: CLAUDE.md project rules, VERSION file, CHANGELOG.md (no code yet).
