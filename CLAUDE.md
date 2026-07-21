# HYPERPLM — Project Rules for Claude

These rules apply to every AI session (Claude, Codex, or any other assistant) and every human contributor working in this repository. Follow them exactly.

## 0. SECRETS — ABSOLUTE RULE

**Claude must NEVER put secrets anywhere public. No exceptions, ever.**

- This repository is PUBLIC. Nothing sensitive goes in any commit, file, issue, PR, comment, or commit message: no tokens, PATs, API keys, passwords, private keys, `.env` contents, database credentials, session secrets, server IPs tied to credentials, or SSH details.
- Before EVERY commit: review the diff for secrets. Before importing ANY external code or config: scan it for embedded secrets first.
- Secrets live only in: local files ignored by `.gitignore` (e.g. `HYPERPLM_PAT.txt`, `.env`), server-side env/config outside the repo, or Docker secrets — never in the image or compose file committed here.
- If a secret ever lands in a commit (even unpushed): stop, tell the user, rotate the secret, and purge it from history before anything else.

## 1. Versioning

- Format: `MM.mm.ppp` (major.minor.patch) — e.g. `00.000.000`
- The project starts at **`00.000.000`**.
- Current version lives in the [VERSION](VERSION) file. Bump it with every change:
  - **patch** (`ppp`) — bug fixes, small tweaks, docs
  - **minor** (`mm`) — new features, new modules
  - **major** (`MM`) — breaking changes, architecture shifts
- Every version bump gets an entry in [CHANGELOG.md](CHANGELOG.md).

## 2. GitHub — every change is documented

- **Every change is committed and pushed to GitHub. No exceptions.**
- Commit and push at the end of every working session, from every machine.
- Commit messages state what changed and why, and reference the new version number.
- Never leave work sitting uncommitted on a local machine.
- Update [CHANGELOG.md](CHANGELOG.md) in the same commit as the change it describes.

## 3. Architecture — main.py + modules

- The application entry point is **`main.py`**. It stays thin: startup, wiring, and dispatch only.
- **All functionality lives in modules.** New features are new modules (or additions to an existing module), imported by `main.py` — never bolted into `main.py` itself.
- One module = one responsibility. Keep interfaces between modules small and explicit.

## 4. Modify, don't rewrite

- **Never completely rewrite the codebase.** Make targeted modifications where needed.
- Preserve working code. If a module needs restructuring, refactor it incrementally in reviewable steps — don't replace it wholesale.
- If a rewrite of any component genuinely seems necessary, stop and get explicit approval from the user first.

## 5. Audits and code checks

- All non-trivial changes get a code check before being considered done. Reviewers can be:
  - a **separate AI session** (fresh context, reviewing the diff),
  - a **different AI** (e.g. Codex reviewing Claude's work, or vice versa), or
  - the **user**.
- The author session does not approve its own work.
- Record who/what performed the review in the changelog entry or commit message.

## 6. Deployment

- HYPERPLM deploys with **Docker** (docker-compose), like the PLM app on VPS 1.
- Target: contractor portal VPS, container bound to `127.0.0.1:4000`, fronted by nginx.
- Domain: **hyperplm.com** (Hostinger). The public landing/coming-soon page lives in `landing/`.
- Runtime secrets are provided via server-side env files outside the repo (see rule 0) — never baked into images or committed compose files.

## 7. Infrastructure reference

- All SSH details, server IPs, domains, ports, and related documents are in the local file **`R:\port_mapping.txt`** (a living document — keep it updated when infrastructure changes).
- **Never copy credentials, keys, passwords, or IPs from that file into this repository.** Reference it by path only. Secrets stay out of git (see rule 0).

## 8. Session checklist

Before ending any working session:

1. Code checked/audited (rule 5)
2. `VERSION` bumped and `CHANGELOG.md` updated (rule 1)
3. Everything committed and pushed to GitHub (rule 2)
