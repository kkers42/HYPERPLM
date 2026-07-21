# Phase 2 Design — Multi-Tenancy + PostgreSQL

**Status:** DRAFT for review (no code written yet).
**Goal:** Turn the single-tenant PLM into a multi-tenant SaaS where every team's data
is hard-isolated — one team cannot see another team's data — backed by PostgreSQL.

This document is the plan. Implementation starts only after review (CLAUDE.md rule 5).

---

## 1. Objectives

1. Introduce **organizations** (teams/tenants). Users belong to one or more orgs.
2. **Hard data isolation** enforced in two layers: application scoping **and** database
   row-level security, so a forgotten `WHERE` clause still cannot leak across tenants.
3. Migrate the datastore from **SQLite → PostgreSQL**.
4. Fold in the non-blocking Phase 1 review follow-ups (see
   [phase1_review_followups.md](phase1_review_followups.md)) while we are in the auth/db layer.

Out of scope (later phases): billing/paywall (Phase 4), full invite/registration UX and
platform-admin console (Phase 3), motorsport domain + tracks (Phase 5).

---

## 2. Why this is a "modify," not a "rewrite"

The core parts/BOM/document logic and router shapes stay. The change is **additive**:
add an `org_id` column and tenant scoping, add two tables, and swap the DB driver. No
production data exists yet (the app is not deployed), so there is **no data migration** —
we stand up the Postgres schema fresh. That removes the single riskiest part of a tenancy
retrofit.

---

## 3. Tenancy model

- **`users`** stay a global identity (email + password_hash + is_active). Authorization is
  no longer global — the `role_id` column moves off `users`.
- **`organizations`** — the tenant boundary (id, name, slug, owner_user_id, created_at).
- **`org_members`** — user↔org join with a per-org role (org_id, user_id, role_id).
  `UNIQUE(org_id, user_id)`. A user can be a member of multiple orgs with different roles.
- **`roles`** gain a nullable `org_id`: `NULL` = system built-in (shared catalog: Owner,
  Admin, Engineer, Viewer); non-null = a custom role owned by that org (future). Ability
  flags (`can_view/write/release/upload/checkout/admin`) are unchanged.
- **Active org**: a user with multiple orgs has one active org per session. The JWT carries
  `active_org_id`; an endpoint lets the user switch. All data operations run against the
  active org.

### Platform admin (you)
"Admin" now means *admin of org X*, not the whole platform. A separate **platform-admin**
capability (super-user, for support/billing) is a distinct concept — a flag on `users`
(e.g. `is_platform_admin`) plus a dedicated, RLS-bypassing access path used only by an
admin console. Full console is Phase 3; the flag lands now so the model is correct.

---

## 4. Data isolation — the core of Phase 2

### Layer 1 — Application scoping
Every request resolves the caller's `active_org_id` (from JWT + a membership check) and
runs data access through a **tenant-scoped handle**: `db = TenantDB(org_id)`. Routers never
pass raw org ids around, so a router cannot forget to scope. New module `app/tenancy.py`.

### Layer 2 — PostgreSQL Row-Level Security (the hard backstop)
Every tenant table gets `org_id BIGINT NOT NULL` and an RLS policy:

```sql
ALTER TABLE parts ENABLE ROW LEVEL SECURITY;
ALTER TABLE parts FORCE ROW LEVEL SECURITY;
CREATE POLICY parts_tenant_isolation ON parts
  USING      (org_id = current_setting('app.current_org')::bigint)
  WITH CHECK (org_id = current_setting('app.current_org')::bigint);
```

At the start of each request's transaction the app runs
`SET LOCAL app.current_org = <active_org_id>`. Then:

- **SELECT / UPDATE / DELETE** are auto-filtered by RLS — no explicit `org_id` needed in
  most queries, which massively cuts the churn across the ~40 data-layer methods.
- **INSERT** must set `org_id`; the `WITH CHECK` clause guarantees you can only insert rows
  into your own org.
- The app connects as a **non-owner role** with `FORCE ROW LEVEL SECURITY`, so RLS applies
  even to the table owner.

This is the direct answer to "the data needs security; one team can't see another team's
data" — it holds even if application code has a bug.

### What this does to the IDOR risk
The Phase 1 audit flagged that `get_part(part_id)` and siblings fetch any row by id with no
ownership check. Under RLS, an id belonging to another org simply returns no row (→ 404).
The whole IDOR class — parts, documents, BOM, file downloads, audit — is closed
structurally, not by per-router checks.

### Tables that get `org_id` + RLS
`parts`, `part_attributes`, `part_revisions`, `part_relationships`, `documents`,
`file_versions`, `audit_log`. (Child tables carry `org_id` directly, rather than only via a
join, so RLS is simple and fast.) `parts` unique constraint changes:
`UNIQUE(part_number)` → **`UNIQUE(org_id, part_number)`** so two teams can reuse part numbers.

### Tables that are NOT tenant-scoped
`users` (global identity), `organizations`, and future global reference data (tracks,
disciplines in Phase 5) — global read, no RLS.

---

## 5. PostgreSQL migration

### Driver / access style
Keep the current thin, raw-SQL data layer (no heavy ORM) — swap `sqlite3` for **psycopg 3**
with a connection pool (`psycopg_pool`). Dialect deltas to handle:

| SQLite | PostgreSQL |
|--------|-----------|
| `INTEGER PRIMARY KEY` autoincrement | `BIGSERIAL` / `GENERATED ... AS IDENTITY` |
| `?` placeholders | `%s` placeholders |
| `PRAGMA journal_mode/foreign_keys/...` | removed (FKs always on) |
| `INSERT ... ON CONFLICT DO UPDATE` | same (Postgres-native) |
| `CURRENT_TIMESTAMP`, recursive CTE | compatible |
| short-lived connections | pooled connections + per-request txn |

A small compatibility shim centralizes placeholder style so the data layer stays readable.

### Connection lifecycle (FastAPI dependency)
Acquire pooled connection → `SET LOCAL app.current_org` → run handlers in one transaction →
commit/rollback → release. Exposed as a `get_db(org_id)` dependency yielding a `TenantDB`.

### Migrations
Replace "apply `schema.sql` at startup" with **versioned migrations**: `db/migrations/NNN_*.sql`
plus a tiny runner (or `yoyo-migrations`). A product's schema will keep evolving; ad-hoc
`executescript` at boot won't cut it.

### Where Postgres runs
Add a **Postgres container** to the app's docker-compose on the contractor VPS
(72.61.0.186), bound to localhost only (the VPS already runs MySQL on 3306; Postgres will
take 5432 internal). Credentials via server-side env / Docker secrets — never in the repo
(CLAUDE.md rule 0). Port map updated when provisioned.

---

## 6. Auth / registration changes (minimum for Phase 2)

Full invite + onboarding UX is Phase 3, but tenancy needs a minimum now:

- **Signup creates an org**: a new user creating an account also creates their org and an
  Owner membership. (Joining an existing org via invite = Phase 3.)
- **Remove "first user = Admin" auto-provision** in the OAuth/Windows paths — provisioning
  is now org-scoped membership, not a global role.
- **JWT** gains `active_org_id`; add `POST /auth/switch-org`.
- `get_current_user` resolves membership + active-org role for the request.

---

## 7. Folded-in Phase 1 follow-ups

Done as part of Phase 2 since we're in these files anyway:
- Rate limiter: count **failed** attempts only; `TRUST_PROXY` flag gating `X-Forwarded-For`;
  evict expired keys; key on `username+IP`.
- Validate `BOOTSTRAP_ADMIN_PASSWORD` against `PASSWORD_MIN_LENGTH`.
- Remove redundant `p != root` check in `files.get_file_path`.
- Migrate `@app.on_event("startup")` → lifespan context manager.

---

## 8. Testing — isolation is the acceptance bar

Add an automated test suite (pytest) with a real Postgres (Docker) that MUST prove:
1. Org A cannot list/read/update/delete Org B's parts, documents, BOM, revisions, audit —
   via API **and** direct-id access (IDOR).
2. RLS negative tests: a raw query without the org GUC set returns nothing / errors.
3. `UNIQUE(org_id, part_number)` lets two orgs share a part number but blocks dupes within one.
4. Org switching changes the visible dataset; a non-member cannot switch into an org.
5. Regression: existing single-org parts/BOM/document/checkout flows still pass.

Isolation tests are the gate — Phase 2 is not "done" until they're green and independently
reviewed.

---

## 9. Proposed module layout (modular per CLAUDE.md)

```
app/
  db.py          # NEW: psycopg pool, connection/transaction lifecycle, placeholder shim
  tenancy.py     # NEW: org/membership logic, TenantDB scoped handle, active-org resolution
  database.py    # MODIFIED: methods take/assume org scope; raw SQL ported to Postgres
  auth.py        # MODIFIED: JWT active_org_id, membership resolution
  routers/
    orgs_router.py   # NEW: create/list orgs, switch active org, list members
db/
  migrations/    # NEW: NNN_*.sql versioned migrations + runner
tests/           # NEW: pytest isolation suite
```

`main.py` stays the thin entry point.

---

## 10. Implementation order (each step reviewable)

1. Stand up Postgres (Docker) + migrations runner; port existing schema to Postgres.
2. Add `organizations`, `org_members`; move role off `users`; add `org_id` + RLS to tenant tables.
3. `app/db.py` pool + per-request org-scoped connection; `TenantDB`.
4. Port `database.py` methods to the scoped connection (SELECT/UPDATE/DELETE via RLS; INSERT sets org_id).
5. Auth: JWT active_org_id, membership resolution, signup-creates-org, `/auth/switch-org`, `orgs_router`.
6. Fold in Phase 1 follow-ups.
7. pytest isolation suite → green. Independent review → merge.

---

## 11. Open decisions (need confirmation before coding)

1. **DB access layer** — raw psycopg 3 + RLS (recommended: stays close to current thin
   style) vs SQLAlchemy Core (more machinery, more portable).
2. **Postgres hosting** — Docker container on the contractor VPS (recommended) vs a managed
   Postgres service.
3. **Multi-org per user** — support many-orgs-per-user in the model now (recommended;
   UX defaults to one) vs single-org-per-user MVP.
4. **Roles** — ship built-in role catalog only for now (recommended) vs per-org custom
   roles immediately.
