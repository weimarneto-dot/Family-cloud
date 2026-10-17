# Security Boundaries — family-cloud

## Trust zones

| Zone | Access | Allowed |
|------|--------|---------|
| localhost (P50) | Trusted | Full API access, job control |
| Tailscale Funnel | Authenticated remote | API + media streaming, valid session required |
| Public internet | Blocked | Nothing; no direct ports exposed |

## Path traversal prevention

- All file-serving endpoints resolve the requested path BEFORE validation:

```
const resolved = path.resolve(root, requested);
if (!resolved.startsWith(root + path.sep)) return 403;
```

- User-supplied filenames are never used as storage paths — only database
  keys map to on-disk locations.

## Access control

- Every endpoint except /healthz and login requires a valid session; deny with
  403 (not 404) so family members see a clear "not allowed" signal.
- Admin-only routes (job submission, user management) check role explicitly.

## Test cases (must pass before any release)

1. GET /files?path=../../etc/passwd returns 403.
2. Unauthenticated request to any media endpoint returns 403.
3. Session token from a revoked user returns 403 immediately.
4. Upload of a file named ../../evil.mp4 is stored under a content key; the
   original name is kept as metadata only.
