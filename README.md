# Family Cloud

A private file cloud for the family — a small Google Drive / iCloud Drive — running on the home
server (the P50) and reached over Tailscale. Same design and same building blocks as Family Chat:
Python standard library only, one SQLite database, accounts you create yourself, installable on the
phone's home screen.

**It does:** logins; folders; upload from phone or computer (big files go in 4 MB pieces and carry
on after a dropped connection); download; pictures, video, audio and PDFs open in the app (video
seeks properly); thumbnails (pictures, and video frames when `ffmpeg` is installed); rename, move,
search by name; **sharing a file or folder with a named person as viewer or editor**; **trash** that
keeps things 30 days; per-person storage limits; a warning when the drive is nearly full.

**It does not do (yet):** sync folders on a computer or WebDAV, automatic photo backup from the phone,
public links for people without an account, end-to-end encryption (the server holds the files in the
clear — protect the drive), version history, resuming an upload after the page was closed or reloaded
(pick the file again), or any antivirus scanning.

**Status:** the server (36 tests) and the web app (a two-user browser test on Chromium) pass in the
build environment. It has **not** been run on the P50, over Tailscale, or on an iPhone yet.

## One login for every family app (single sign-on)

Start the server with `--sso http://127.0.0.1:8766` (Downloader Web's address) and Downloader Web's accounts sign in
here too. A person who is signed in to Downloader Web, Player, Song Studio, Chat or any other app that uses this
already carries the `dlw_session` cookie (cookies are shared by every port of the same host name), so Cloud opens
without asking again. Cloud only asks Downloader Web who the cookie belongs to; passwords are checked there alone.

- Typing an **e-mail address** in the login box signs in with the family login; a plain username still uses the local account.
- The first visit creates a user from the e-mail (`anna@example.org` becomes `anna`, or `anna2` if `anna` exists), named as in
  Downloader Web. It never takes over an existing user. To make an existing user (and their data) belong to an address:
  `python3 server.py link-user anna anna@example.org`.
- Signing out signs out of every family app.
- Requests from the public internet (Tailscale Funnel) cannot sign in with a family password; local accounts still work.
- Without `--sso` nothing changes. `python3 test_sso.py` tests it against a stand-in Downloader Web.

## Using the P50's resources

- **Disk:** `--data-dir` can be on any drive; files live in `blobs/` under it by id, so renaming never
  moves bytes. `--require-mount /mnt/drive` refuses uploads (503) if that drive is not mounted, so a
  missing disk never fills the system disk. Uploads are refused (507) when less than 5 GB would be
  left (`FAMILY_CLOUD_FREE_MARGIN_GB`). Largest single file 50 GB (`FAMILY_CLOUD_MAX_FILE_GB`).
- **Per-person limits:** `add-user --quota-gb 200`, or later `set-quota anna 200` (0 = only the drive).
- **CPU:** thumbnails use `ffmpeg` at low priority, as many at once as the P50 has cores
  (`FAMILY_CLOUD_WORKERS`). The service runs at `Nice=10` and idle disk priority so Downloader jobs win.
- **Memory:** files are streamed in chunks, never loaded whole.

## First run (on the P50)

```bash
git clone <this repository> ~/family-cloud && cd ~/family-cloud
python3 server.py --data-dir /mnt/BIGDRIVE/family-cloud add-user anna --name "Anna" --quota-gb 500
python3 server.py --data-dir /mnt/BIGDRIVE/family-cloud add-user ben  --name "Ben"
python3 server.py --data-dir /mnt/BIGDRIVE/family-cloud serve      # http://127.0.0.1:8778/
```

Other commands: `list-users`, `reset-password`, `disable-user`, `set-quota`. There is no sign-up page.

## From the phones (Tailscale)

Phones only install the app and keep the login over HTTPS. Give the cloud its own HTTPS port on the
tailnet, next to Chat (8443) — check `tailscale serve --help` for the exact syntax on your version:

```bash
sudo tailscale serve --bg --https=8444 http://127.0.0.1:8778
tailscale serve status
```

Then open `https://fedora.taildc5fc1.ts.net:8444/` on each phone and use Share → Add to Home Screen.
Do **not** use Funnel: that would put everyone's files on the public internet.

To keep it running: edit the two paths in `family-cloud.service`, copy it to
`~/.config/systemd/user/`, then `systemctl --user daemon-reload && systemctl --user enable --now family-cloud`.

## Back up

The data directory is the cloud: the `cloud.db` database **and** the `blobs/` folder (and `thumbs/`, which can be rebuilt) next to it. Back
up both together (a database without its files, or the reverse, is not a backup).

## Security notes

Passwords: scrypt. Sessions: random tokens stored hashed, 30 days. Every POST needs the
`X-Requested-With: family-cloud` header and a matching `Origin`; cookie is `HttpOnly`, `SameSite=Strict`.
Names are shown as text, never HTML; strict Content-Security-Policy; only safe types are displayed
inline, everything else downloads. Paths never come from the user, so there is nothing to traverse.
Repeated wrong passwords lock that user/address pair out for a while.

## Tests

```bash
python3 test_cloud.py                                   # server, 36 tests
npm install && CHROMIUM=/path/to/chrome node tests/smoke.js   # browser, two users, port 8789
```
