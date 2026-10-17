# Technology Stack — family-cloud

Verified against the actual code (README, server.py, family-cloud.service).
TODO = decided in code but not yet documented here.

| # | Decision | Choice (verified) | Verification |
|---|----------|------------------|--------------|
| 1 | Frontend | Static web app (static/), installable on the phone home screen | Opens on phone; login survives restart |
| 2 | Backend | Python standard library only (server.py), port 8778 | python3 test_cloud.py — 36 tests pass |
| 3 | Database | Single SQLite database (cloud.db) | Store + retrieve across restart |
| 4 | File storage | blobs/ by id on any drive (--data-dir); --require-mount refuses uploads (503) when drive missing; 507 under 5 GB margin; 50 GB max file | Files persist; guards fire |
| 5 | Auth | scrypt passwords; hashed session tokens, 30 days; X-Requested-With header + Origin check; HttpOnly SameSite=Strict cookie | test_cloud.py auth tests |
| 6 | Thumbnails | ffmpeg at low priority; workers = cores (FAMILY_CLOUD_WORKERS) | Thumbnails render for pictures/video |
| 7 | Hosting | systemd user service (family-cloud.service), Nice=10, on the P50 | systemctl --user status family-cloud |
| 8 | Remote access | tailscale serve HTTPS on the tailnet (port 8444). NOT Funnel — no public exposure | Opens on phone via tailnet URL |
| 9 | Uploads | 4 MB chunked, resume across dropped connections; 507/503 guards | Large upload survives disconnect |
| 10 | Tests | test_cloud.py (36) + browser smoke test (tests/smoke.js, Chromium) | Both suites green |

## Rationale log

- Python stdlib only: same design and building blocks as Family Chat; nothing
  to install or patch on the P50.
- Single SQLite database: one metadata file to back up next to blobs/.
- tailscale serve, not Funnel: Funnel would put everyone's files on the public
  internet (explicit README decision).
