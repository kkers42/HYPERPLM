# Changelog

All notable changes to HYPERPLM are documented here. Every entry corresponds to a version bump in [VERSION](VERSION).

Format: `MM.mm.ppp — YYYY-MM-DD — description — reviewed by`

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
