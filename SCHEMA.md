# Data Model — family-cloud

## Entities

| Entity | Purpose | Key fields (v1) |
|--------|---------|----------------|
| User | Household member | id, name, role (admin/member), created_at |
| Session | Login state | id, user_id, token_hash, expires_at |
| MediaFile | A stored file | id, storage_path, mime_type, size_bytes, checksum, uploaded_by, created_at |
| Playlist | Ordered collection | id, name, owner_id, visibility |
| PlaylistItem | Membership | playlist_id, media_file_id, position |
| Job | Pipeline task | id, type (transcribe/download), status (queued/running/done/failed), payload, created_at, finished_at |

## Relationships

- User 1—N MediaFile (uploaded_by)
- User 1—N Session
- Playlist N—M MediaFile via PlaylistItem
- Job N—1 MediaFile (target file, nullable)

## Storage layout

- App state: SQLite (single file, trivially backup-able).
- Job queue: jobs.json (working queue) + downloader_run_history.json
  (append-only run history); both included in config backups.
- Media files: flat content-addressed directories — no user-supplied paths;
  original filenames stored only as metadata.

## Sample records (test fixtures)

```json
{ "user":    { "id": 1, "name": "Weimar", "role": "admin" },
  "media":   { "id": 42, "storage_path": "ab/cdef1234", "mime_type": "video/mp4",
               "size_bytes": 8300000, "checksum": "sha256:..." },
  "job":     { "id": 7, "type": "transcribe", "status": "running",
               "payload": { "media_file_id": 42 } } }
```
