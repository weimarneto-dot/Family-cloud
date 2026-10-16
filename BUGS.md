# Bug register: family-cloud

> The autosave hook (`.git/hooks/post-commit`), if installed, must carry the default-branch guard (BUG-0002). Without it a commit on a branch tracking `main` is published to `main`.

Every bug found in family-cloud, worked through the debugging protocol (`DEBUGGING.md` in the Downloader repository, D-1..D-10).
IDs (`BUG-####`) are per repository, count up, and are never reused. Entry format: see `DEBUGGING.md`.

### BUG-0002 - The autosave hook could publish to a shared default branch (the trap behind the family-chat incident)
- Severity: S2          - Status: closed
- Opened: 2026-10-16 (the family-chat incident: an unreviewed commit reached main; family-chat BUG-0001)
- Evidence: `.git/hooks/post-commit` pushes HEAD to the current branch's upstream when `push.autosave=true`. A local `main` tracked `origin/main` here, so a commit on it would have been published. Reproduced in a scratch repo before the fix: remote main moved to the new commit.
- Verify: a scratch repo with `main` tracking a bare remote, `push.autosave true`, the hook copied in, one commit: before the guard, the remote's `main` moves.
- Fix: the hook refuses to push when the upstream branch is `main`, `master`, `HEAD`, `trunk` or `develop` (the case statement after `remote=`/`rbranch=`); the local `main` upstream is removed in this repo. The hook lives in `.git/hooks` and is not versioned: the copy in this repo is the one this entry describes.
- Check: the same scratch test: remote `main` stays at its first commit; a feature branch still autosaves to its own upstream.
- Done: 2026-10-16 on this machine. Not verified: the other machines that clone this repo (each needs the same hook).
