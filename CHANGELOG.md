# Changelog

All notable changes to HYPERPLM are documented here. Every entry corresponds to a version bump in [VERSION](VERSION).

Format: `MM.mm.ppp — YYYY-MM-DD — description — reviewed by`

## 00.002.003 — 2026-07-21

- Finalize Phase 2 design decisions in docs/phase2_design.md: (1) SQLAlchemy Core over
  psycopg raw (data layer reworked to Core + Alembic migrations), (2) Postgres in Docker on
  contractor VPS, (3) multi-org-per-user model now, (4) per-org custom roles now (roles
  becomes tenant-scoped with org_id + RLS). Design ready to implement pending final review.
- Reviewed by: user (resolved the 4 open decisions).

## 00.002.002 — 2026-07-21

- Add docs/phase2_design.md — DRAFT design for Phase 2 (multi-tenancy + PostgreSQL).
  Covers org/membership model, two-layer data isolation (app scoping + Postgres RLS as
  hard backstop, which structurally closes the IDOR class), SQLite->Postgres migration
  (psycopg 3 + versioned migrations, no data migration since not yet deployed), minimal
  auth changes, folded-in Phase 1 follow-ups, isolation test plan, module layout, and
  4 open decisions. No code yet — awaiting review.
- Reviewed by: PENDING (design draft for user/AI review before implementation).

## 00.002.001 — 2026-07-21

- Record independent review of Phase 1 in docs/phase1_review_followups.md (satisfies
  CLAUDE.md rule 5). Verdict: safe to ship; filed non-blocking follow-ups (rate limiter
  refinements, bootstrap password validation, lifespan migration, redundant path check).
- Reviewed by: user (independent review pass).

## 00.002.000 — 2026-07-21 — Phase 1: Security Hardening

Engine-agnostic hardening ahead of the multi-tenant + PostgreSQL migration (Phase 2).

- **SECRET_KEY fail-fast**: new `config.validate()` raises and refuses startup in
  production if SECRET_KEY is unset/default/<32 chars (JWTs are forgeable otherwise,
  and this repo is public). Warns instead of failing in development. Also validates
  Google OAuth creds and https base URL. Called on app startup (main.py).
- **No default admin in production**: removed the hardcoded `admin`/`admin123` seed.
  First admin now comes from BOOTSTRAP_ADMIN_USERNAME/PASSWORD env (forced password
  change on first login). Production with no bootstrap creds creates no account;
  dev keeps a throwaway admin with a loud warning. (database.py)
- **Login rate limiting**: new app/security.py in-memory sliding-window limiter
  (10 attempts / 5 min / client IP, X-Forwarded-For aware) applied to /auth/login
  and /auth/windows. Returns 429 + Retry-After.
- **Path-traversal guard hardened**: files.get_file_path now uses Path.is_relative_to
  instead of str.startswith, closing the sibling-prefix bypass (e.g. /srv/plm-files-evil).
- **Security headers middleware**: X-Content-Type-Options, X-Frame-Options=DENY,
  Referrer-Policy, and HSTS (prod+https). CSP deferred until the inline-script
  frontend is reworked.
- **Password policy centralized**: config.PASSWORD_MIN_LENGTH (default 8), enforced in
  change-password.
- App title renamed PLM Lite -> HYPERPLM. .env.example documents all new vars.
- Verified: py_compile clean; functional tests pass (prod fail-fast, dev warn, limiter
  10-then-429, path guard blocks traversal + sibling-prefix); full app imports with
  middleware wired.
- Reviewed by: Claude (author session) + automated functional tests. PENDING independent
  review by separate AI session or user per CLAUDE.md rule 5.

## 00.001.004 — 2026-07-21

- LICENSE: set copyright holder to legal name Joshua M. Grace.
- Reviewed by: user (provided legal name).

## 00.001.003 — 2026-07-21

- Add LICENSE: proprietary, All Rights Reserved (relicensed from the prior MIT "PLM Lite"
  by the same owner; prior MIT grant applies only to prior PLM Lite releases).
- README: retitled PLM Lite -> HYPERPLM, replaced "MIT License — Open Source" with
  proprietary notice.
- static/index.html: replaced "Open Source on GitHub / PLM Lite V1.0" footer with
  HYPERPLM All Rights Reserved notice.
- Reviewed by: user (chose proprietary license).

## 00.001.002 — 2026-07-21

- Landing page rethemed for motorsports: racing red/amber palette, hero and all
  capability copy rewritten for race teams (build trees, spares, setup sheets,
  trailer pack lists, tech inspection). Deployed live to hyperplm.com (HTTP;
  SSL pending user-run certbot).
- Reviewed by: user (directed motorsports focus).

## 00.001.001 — 2026-07-21

- CLAUDE.md: add Rule 0 — Claude must NEVER put secrets anywhere public (absolute rule,
  pre-commit diff review required, incident procedure defined).
- CLAUDE.md: add Deployment section — Docker via docker-compose, contractor portal VPS,
  127.0.0.1:4000 behind nginx, domain hyperplm.com (Hostinger).
- Add landing/index.html — self-contained "Coming Soon" page for hyperplm.com listing
  platform capabilities.
- Reviewed by: user (directed changes).

## 00.001.000 — 2026-07-21

- Import baseline codebase: PLM Lite V1.0 (MIT) pulled from the live deployment at
  3dprintdudes.io/plm (VPS1 /opt/plm working tree, commit 46f5e36 + 2 untracked files).
  FastAPI + vanilla JS: parts, BOM/relationships, documents, auth (local/Google OAuth),
  check-in/out, release status, Excel export.
- Excluded from import: .env (secrets), deploy.sh (server-side artifact), .git history,
  __pycache__. Merged upstream .gitignore rules; un-ignored .env.example template.
- Reviewed by: user (directed import of running version as starting point).

## 00.000.001 — 2026-07-21

- Add .gitignore: excludes PAT/token files, keys, .env, and Python artifacts (repo is public).
- Reviewed by: user (requested during setup).

## 00.000.000 — 2026-07-21

- Initial repository setup: CLAUDE.md project rules, VERSION file, CHANGELOG.md.
- Reviewed by: user (initial setup, no code yet).
