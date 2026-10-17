# PROTOCOL.md — family-cloud

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
