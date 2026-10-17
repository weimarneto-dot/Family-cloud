# Technology Stack — family-cloud

Fill in every TODO before writing feature code. Update the Rationale log whenever
a choice changes.

| # | Decision | Question | Choice | Version pinned | Verification Method |
|---|----------|----------|--------|----------------|---------------------|
| 1 | Frontend | Web, mobile, desktop? | Web PWA (works on phones + desktop) | TODO | Installs to phone home screen; loads via Tailscale |
| 2 | UI framework | React / Vue / Svelte / etc.? | TODO | TODO | Hello World renders on phone |
| 3 | Backend | Node / Python / Go / etc.? | TODO | TODO | GET /healthz responds 200 OK |
| 4 | Database | SQL / NoSQL / file-based? | SQLite (embedded, fits P50, easy backups) | TODO | Store + retrieve a test record across restart |
| 5 | Media storage | Local / remote / both? | Local disk on P50, rsync-backed | TODO | Files persist across restarts |
| 6 | Auth | Sessions / Tokens / OAuth? | Tailscale identity + app-level session tokens | TODO | Login state survives app restart |
| 7 | Hosting | Self-hosted / cloud? | Self-hosted on P50 (systemd user service) | TODO | Service accessible at Tailscale Funnel URL |
| 8 | Updates | Manual / PWA / auto? | PWA service-worker update prompt + rollback | TODO | New version loads without hard reload |
| 9 | Transcription | Whisper variant? | whisper.cpp or faster-whisper on P50 | TODO | Sample audio produces transcript job result |

## Rationale log

- **SQLite:** single-file database, trivial to back up alongside config, no extra
  service to babysit on the P50.
- **Systemd user service:** keeps the app under user control; KillMode=process
  preserves running jobs across service restarts.
- TODO: add one rationale entry per row as choices are finalized.
