# Data Model — family-cloud

Source of truth: server.py (schema) and test_cloud.py (behavior). The notes
below are verified from the README; confirm exact field names in server.py
before coding against them.

## Entities

| Entity | Purpose | Notes (verified) |
|--------|---------|------------------|
| User | Household account | username, display name, per-person quota (add-user --quota-gb); no sign-up page |
| Session | Login state | random token stored hashed, 30 days; cookie HttpOnly SameSite=Strict |
| Folder | Directory tree | per-person tree in SQLite |
| File | Stored file | content lives in blobs/ by id; rename/move never copies bytes |
| Share | Viewer/editor grant | with a named person; inherited down folders; editors cannot touch the share root |
| Trash entry | Soft delete | kept 30 days |

## Relationships

- User 1—N Folder/File (per-person files and folders)
- User 1—N Session
- Share N—1 User (grantee); Share N—1 Folder/File (target)

## Storage layout

- cloud.db: all metadata.
- blobs/: file content by id, under --data-dir (any drive).
- thumbs/: thumbnails, rebuildable.
- Back up cloud.db and blobs/ together — one without the other is not a backup.

## Sample records

TODO: after first run on the P50, paste one real row per table here (redacted).
