# Phase 1 Security Hardening — Review & Follow-ups

Independent review of `00.002.000` (Phase 1). Verdict: **safe to ship**; the traversal
fix, SECRET_KEY fail-fast, and removal of hardcoded credentials are all correct and
meaningful. Items below are non-blocking; address in a future update.

Satisfies CLAUDE.md rule 5 (independent code check by a separate reviewer).

## Bugs / correctness
- [x] `files.get_file_path`: removed the redundant `p != root` guard (2026-07-28, step 6).
- [x] `Path.is_relative_to` requires Python 3.9+ — we target 3.12; documented, no action.
- [x] `@app.on_event("startup")` deprecation → migrated to the lifespan context manager in
      main.py (step 5).

## Rate limiter (app/security.py) — resolved step 6 (2026-07-28)
- [x] Failure-only counting for login/windows: a guard dependency checks without recording,
      the handler records only on rejected credentials (note_login_failure). Successful
      logins no longer accumulate toward a lockout. Registration counts all attempts
      (separate limiter, 10/hour).
- [x] `X-Forwarded-For` now honored only when `TRUST_PROXY` is set (default off), so a
      direct-exposed deployment can't be spoofed.
- [x] `_hits` is evicted lazily (empty deques dropped on prune) with a key-count sweep cap —
      no unbounded growth.
- [ ] Keying on `username + IP` instead of IP alone — deferred (needs request-body access in
      the dependency). IP-based failure counting addresses the lockout concern for now.
- Note: still in-memory / per-process. Multi-worker/host scale needs a shared store (Redis).

## Security headers
- [x] `X-XSS-Protection: 0` is intentional — disabling the legacy auditor is current best
      practice, not a leftover.
- [ ] No CSP (inline scripts in current static pages). Deferred until the frontend is
      reworked with a nonce-based CSP.

## Consistency
- [ ] `_seed_bootstrap_admin` does not validate `BOOTSTRAP_ADMIN_PASSWORD` length, so a weak
      bootstrap password slips past the `PASSWORD_MIN_LENGTH` policy enforced on
      change-password. Enforce the same minimum on the bootstrap password.
