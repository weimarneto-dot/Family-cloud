# Architecture — family-cloud

## Component diagram

User Device (phone / computer on the Tailscale tailnet)
        |
        +---> [C6] tailscale serve --https=8444 (TLS, tailnet only; NOT Funnel)
                    |
                    +---> [C2] server.py (Python stdlib HTTP, 127.0.0.1:8778)
                              |
                              +---> [C3] cloud.db (SQLite: users, sessions,
                              |        folder/file tree, shares, trash)
                              +---> [C4] blobs/ (file content by id, on any
                              |        drive via --data-dir)
                              +---> [C5] thumbs/ (ffmpeg thumbnails, low
                                       priority, rebuildable)

[C1] the web app (static/) is served by [C2].

## Numbered component references

- **C1 — Web app (static/).** Talks only to C2 over HTTPS on the tailnet.
- **C2 — server.py.** The only code surface: logins, chunked uploads, ranged
  downloads, thumbnails, rename/move/search, sharing, trash, quotas,
  free-space margin.
- **C3 — cloud.db.** All app state. Back up together with C4.
- **C4 — blobs/.** Immutable content by id; rename/move never touches bytes.
- **C5 — thumbs/.** ffmpeg at low priority (service runs Nice=10 so Downloader
  jobs win); as many workers as cores. Rebuildable.
- **C6 — tailscale serve.** Terminates TLS on tailnet port 8444. Funnel is
  explicitly NOT used: no public internet exposure.

## Data flow example

1. Phone opens the app: C6 -> C2 serves C1; login session from C3.
2. Upload: C1 sends 4 MB chunks -> C2 writes into C4 -> metadata into C3.
3. Share: C2 records viewer/editor grant in C3, inherited down folders;
   editors cannot touch the share root.
4. Delete: C2 moves the entry to trash in C3; kept 30 days.

## Neighbouring services

Other family-cloud services on the same P50 (Whisper transcription, Downloader
job queue — jobs.json, downloader_run_history.json) run under their own
systemd units and are documented separately.
