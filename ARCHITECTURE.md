# Architecture — family-cloud

## Component diagram

```
User Device (Phone / Linux / Windows)
        |
        |---> [C1] fMily Play App (PWA frontend)
        |             |
        |             +---> [C2] API Gateway (backend on P50)
        |                        |
        |                        +---> [C3] Database (SQLite)
        |                        +---> [C4] Media Storage (local disk)
        |                        +---> [C5] Media Pipeline (Whisper / job queue)
        |
        +---> [C6] Tailscale Funnel (authenticated remote access)
```

## Numbered component references

- **C1 — fMily Play frontend.** PWA served as static files. Talks only to C2
  over HTTPS; never directly to C3/C4/C5.
- **C2 — API Gateway.** The only public-facing code surface. Validates paths
  (see SECURITY.md), checks sessions, and enqueues jobs.
- **C3 — Database.** App state: users, sessions, job records, file metadata.
- **C4 — Media storage.** Immutable original files; keyed paths only.
- **C5 — Media pipeline.** Worker(s) consuming jobs.json; writes run history
  to downloader_run_history.json. Runs under systemd with KillMode=process so
  active jobs survive service restarts.
- **C6 — Tailscale Funnel.** Terminates TLS; forwards to C2. No router port
  forwarding; the public internet cannot reach anything else on the P50.

## Data flow example (MVP slice)

1. C1 requests the file list: C1 -> C2 -> C3 (metadata) -> C1 renders list.
2. C1 requests playback: C2 streams from C4 -> C1 plays.
3. Transcription: C2 enqueues job in C5 -> C5 writes result to C4 and status
   to C3 -> C1 polls or receives update.
