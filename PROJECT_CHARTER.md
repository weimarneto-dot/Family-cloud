# Project Charter — family-cloud

Last updated: 2026-10-17 · Owner: Weimar Neto · Status: Active

## Problem (one sentence)

The family needs a private place to store, share, and reach its files from any
device — a small self-hosted Google Drive / iCloud Drive — without third-party
cloud services.

## Primary user (one specific profile)

The household: 2–3 non-technical family members who use the app from their
phones (installed to the home screen) and never need to know the server exists;
administered by Weimar Neto on the P50.

## Top 3 use cases

1. Upload from phone or computer — big files go in 4 MB pieces and carry on
   after a dropped connection — and reach them from any device.
2. Share a file or folder with a named family member as viewer or editor.
3. Deleted things are recoverable: trash keeps them 30 days.

## Explicitly out of scope for v1 (from README)

1. Sync folders on a computer or WebDAV.
2. Automatic photo backup from the phone.
3. Public links for people without an account.
4. End-to-end encryption (server holds files in the clear — protect the drive).
5. Version history.
6. Resuming an upload after the page was closed or reloaded.

## 60-second check

If you cannot explain this charter to a family member in under a minute,
simplify the Problem sentence — not the use cases.
