# Data Model — family-cloud

Source of truth: the SCHEMA constant in server.py and test_cloud.py. This
document mirrors it; if code and this file disagree, the code wins and this
file must be fixed.

## Tables (verified against server.py, 2026-10-17)

| Table | Purpose | Key columns |
|-------|---------|-------------|
| users | Household account | id, username (unique, 2–32 chars a-z0-9_.-), display_name, pw_hash (scrypt), quota_bytes, disabled, created |
| sessions | Login state | token_hash (sha256 of token, PK), user_id, created, last_seen; valid 30 days |
| items | Folder tree and files — ONE table, not separate entities | id, owner_id, parent_id (NULL = root), name (unique per parent, case-insensitive, among un-trashed), kind ('file'/'folder'), size, mime, blob, sha256, created, modified, trashed (NULL = live; timestamp = in trash) |
| shares | Viewer/editor grants | item_id + user_id (PK), role ('view'/'edit'), created; inherited down folders |
| uploads | Chunked upload state | id (32-hex), user_id, owner_id, parent_id, name, size, received, mime, created; stale after 24 h |

## Trash model

Trash is not a table: an item with trashed set is in the trash, kept 30 days
(TRASH_DAYS), purged with its subtree. A trashed item is not shared anymore.

## Storage layout (on --data-dir, any drive)

- cloud.db — all metadata (SQLite, WAL mode)
- blobs/<first 2 hex>/<blob> — file content by id; rename/move never copies bytes
- tmp/ — in-flight chunked uploads
- thumbs/<blob>.jpg — ffmpeg thumbnails, rebuildable
- Back up cloud.db together with blobs/ — one without the other is not a backup.

## Sample records

TODO: after first run on the P50, paste one real row per table here (redacted).
