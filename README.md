# family-cloud

Self-hosted family cloud: media storage, streaming, and file management for the
household. Runs on a P50 workstation as systemd user services, accessed remotely
through Tailscale Funnel. Frontend app: **fMily Play** (PWA).

## Status

Phase 1 — Foundation. Scope and roadmap: see PROJECT_CHARTER.md.

## Documentation index

| Document | Purpose |
|---|---|
| PROJECT_CHARTER.md | Problem, primary user, top 3 use cases, out-of-scope list |
| TECH_STACK.md | Technology decisions with verification methods |
| ARCHITECTURE.md | Component diagram with numbered references |
| SCHEMA.md | Entities, relationships, storage layout, sample records |
| SECURITY.md | Trust zones, path traversal prevention, access rules |
| PROTOCOL.md | Version control protocol + debug protocol |
| DEBUG-REPORT.md | Issue tracking (Claim #N format) |

## Repository layout

    src/       All source code
    bin/       CLI scripts and utilities
    tests/     Unit, integration, e2e tests
    docs/      Documentation
    config/    Configuration files
    deploy/    Deployment scripts

## Quickstart

<!-- TODO: add once the MVP slice exists -->

## License

Private family project — all rights reserved.
