# PROTOCOL.md — family-cloud

## Section 0: Remote Actions (Standing Rule)

GitHub remote actions are forbidden until you have listed the existing branches
and tags and confirmed with the owner what the default branch state is.

- All pushes go to a NEW review branch — never to main, never to an existing
  branch.
- If a push to a non-empty repo would overwrite anything, stop and report.
- No force-push, no history rewrite, ever.
- The owner merges review branches with git merge --no-ff after review.

Adopted 2026-10-17, after an AI session overwrote README.md and .gitignore on
main by assuming a blank slate (commits 7d1a4e2 and 1faf60a). The overwrite was
fully restored — verified by identical git blob SHAs against b4495c2 — but the
review gate should have prevented it, not the recovery.

## Section 1: Version Control Protocol

- Branches: feat/description-date, fix/description-date,
  claude/session-description.
- Local commits are held for review before push; push only via automated
  nightly sync or explicit approval.
- Integrate features with git merge --no-ff.

## Section 2: Debug Protocol

- Verification before action: verify every claim against actual code or tests
  before marking anything done or resolved.
- New issues are logged in BUGS.md (BUG-000N entries: found date, repro steps,
  expected, actual, status, resolution).
- A fix is only marked resolved after it is verified against the real code,
  and a regression test is added for each resolved issue.
- Push only after the verification steps above pass.
