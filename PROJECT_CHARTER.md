# Project Charter — family-cloud

Last updated: 2026-10-17 · Owner: Weimar Neto · Status: Draft

## Problem (one sentence)

family-cloud lets the household store, stream, and manage its own media and
files from any device, without depending on third-party cloud services.

## Primary user (one specific profile)

Weimar Neto — technical owner and administrator of the self-hosted server —
plus 2–3 non-technical family members who use the apps from phones and laptops
and should never need to know the server exists.

## Top 3 use cases

1. **Watch/listen anywhere:** A family member opens the fMily Play app on their
   phone (via Tailscale) and plays a video or audio file stored on the P50.
2. **Add media easily:** New media is uploaded or downloaded via the job queue
   and becomes available to everyone after automatic processing (e.g. Whisper
   transcription).
3. **Reliable remote access:** Content keeps working on flaky mobile
   connections — partial uploads never corrupt files, and queued actions sync
   when the connection returns.

## Explicitly out of scope for v1

1. Public internet access / user registration for strangers.
2. Multi-tenant support beyond the immediate household.
3. Native mobile apps (v1 is a PWA).
4. Automatic media library scraping / metadata enrichment.
5. Real-time collaboration features.

## 60-second check

If you cannot explain this charter to a family member in under a minute,
simplify the Problem sentence — not the use cases.
