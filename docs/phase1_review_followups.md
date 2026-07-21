# Phase 1 Security Hardening — Review & Follow-ups

Independent review of `00.002.000` (Phase 1). Verdict: **safe to ship**; the traversal
fix, SECRET_KEY fail-fast, and removal of hardcoded credentials are all correct and
meaningful. Items below are non-blocking; address in a future update.

Satisfies CLAUDE.md rule 5 (independent code check by a separate reviewer).

## Bugs / correctness
- [ ] `files.get_file_path`: the `p != root` guard is redundant — `Path.is_relative_to`
      already returns True for equal paths. Remove it.
- [ ] `Path.is_relative_to` requires Python 3.9+. We target Python 3.12 (Orion/VPS), so
      this is fine — but if the support floor ever drops to 3.8, this breaks. Documented,
      no action needed unless the target changes.
- [ ] `@app.on_event("startup")` is deprecated in current FastAPI. Migrate to the lifespan
      context manager when convenient. Not urgent.

## Rate limiter (app/security.py)
- [ ] Counts **all** attempts, not just failures — 10 rapid successful logins from one IP
      lock out real users. Count only failures, or reset the bucket on success.
- [ ] 10/5min per IP is aggressive for shared-office / NAT'd clients behind one public IP.
      Consider keying on `username + IP` instead of IP alone.
- [ ] `X-Forwarded-For` is trusted whenever present. On a direct-exposed deployment a client
      can spoof it to evade the limit or poison another IP's bucket. Gate XFF trust behind a
      config flag (e.g. `TRUST_PROXY`, default off) rather than always honoring it.
- [ ] `_hits` grows unbounded — expired keys are never evicted (memory leak under many
      distinct IPs). Add periodic/lazy eviction of empty deques.
- Note: this in-memory limiter is per-process. Multi-worker/multi-host scale needs a shared
      store (Redis). Already flagged in the module docstring.

## Security headers
- [x] `X-XSS-Protection: 0` is intentional — disabling the legacy auditor is current best
      practice, not a leftover.
- [ ] No CSP (inline scripts in current static pages). Deferred until the frontend is
      reworked with a nonce-based CSP.

## Consistency
- [ ] `_seed_bootstrap_admin` does not validate `BOOTSTRAP_ADMIN_PASSWORD` length, so a weak
      bootstrap password slips past the `PASSWORD_MIN_LENGTH` policy enforced on
      change-password. Enforce the same minimum on the bootstrap password.
