# Bug register: family-cloud

> The autosave hook (`.git/hooks/post-commit`), if installed, must carry the default-branch guard (BUG-0002). Without it a commit on a branch tracking `main` is published to `main`.

Every bug found in family-cloud, worked through the debugging protocol (`DEBUGGING.md` in the Downloader repository, D-1..D-10).
IDs (`BUG-####`) are per repository, count up, and are never reused. Entry format: see `DEBUGGING.md`.

### BUG-0001 - The suites fail with "the server's drive is full" when the temp directory has less than 5 GB free
- Severity: S3          - Status: closed
- Opened: 2026-10-15 (first scorecard run, Q1; recorded first in the Downloader register as its BUG-0009, moved here because the bug lives in this repo)
- Evidence: 2026-10-15 15:39, `python3 test_cloud.py` and `test_photos.py` at family-cloud `a00574e`: `FAILED (failures=4, errors=19)` and `failures=3`, `AssertionError: 507 != 201 : {'error': "The server's drive is full."}`. The server refuses an upload when the free space minus the 5 GB margin (`FAMILY_CLOUD_FREE_MARGIN_GB`, server.py `FREE_MARGIN`) is less than the file, and the temp directory had less than 5 GB free.
- Verify: `df -h /tmp`; `python3 -m unittest test_cloud` (before the fix: 23 of 36 fail with 507).
- Fix: the suites set `FAMILY_CLOUD_FREE_MARGIN_GB=0` for their own server (test_cloud.py and test_photos.py, before `import server`). The margin stays the real server's rule: the quota test still checks it through `cloud.FREE_MARGIN`.
- Check: `python3 -m unittest test_cloud` (36 tests OK) and `python3 -m unittest test_photos` (4 tests OK), with `/tmp` 3.6 GB free (below the 5 GB margin).
- Done: 2026-10-16 on bug/cloud-test-free-margin: test_cloud 36/36, test_photos 4/4. Not verified: a run with `/tmp` at 90% used (the register's own check); the real drive's margin is unchanged.

### BUG-0002 - The autosave hook could publish to a shared default branch (the trap behind the family-chat incident)
- Severity: S2          - Status: closed
- Opened: 2026-10-16 (the family-chat incident: an unreviewed commit reached main; family-chat BUG-0001)
- Evidence: `.git/hooks/post-commit` pushes HEAD to the current branch's upstream when `push.autosave=true`. A local `main` tracked `origin/main` here, so a commit on it would have been published. Reproduced in a scratch repo before the fix: remote main moved to the new commit.
- Verify: a scratch repo with `main` tracking a bare remote, `push.autosave true`, the hook copied in, one commit: before the guard, the remote's `main` moves.
- Fix: the hook refuses to push when the upstream branch is `main`, `master`, `HEAD`, `trunk` or `develop` (the case statement after `remote=`/`rbranch=`); the local `main` upstream is removed in this repo. The hook lives in `.git/hooks` and is not versioned: the copy in this repo is the one this entry describes.
- Check: the same scratch test: remote `main` stays at its first commit; a feature branch still autosaves to its own upstream.
- Done: 2026-10-16 on this machine. Not verified: the other machines that clone this repo (each needs the same hook).
