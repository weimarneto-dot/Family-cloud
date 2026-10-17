# Security — family-cloud

Verified from the README security notes; details in server.py.

## Trust zones

| Zone | Access | Allowed |
|------|--------|---------|
| localhost (P50) | Trusted | server.py on 127.0.0.1:8778; admin CLI (add-user, set-quota, ...) |
| Tailscale tailnet | Household devices | HTTPS via tailscale serve :8444 |
| Public internet | Blocked | Nothing — Funnel deliberately not used |

## Mechanisms (as implemented)

- Passwords hashed with scrypt; repeated wrong passwords lock that user/address
  pair out for a while.
- Sessions: random tokens stored hashed, 30 days.
- CSRF: every POST needs the X-Requested-With: family-cloud header and a
  matching Origin; cookie is HttpOnly, SameSite=Strict.
- XSS: names shown as text, never HTML; strict Content-Security-Policy; only
  safe types displayed inline, everything else downloads.
- Path traversal: paths never come from the user — nothing to traverse.

## Release checklist

1. python3 test_cloud.py passes (36 tests).
2. Unauthenticated request to any file endpoint is refused.
3. POST without the X-Requested-With header is refused.
4. Service not reachable from the public internet (Funnel off).
