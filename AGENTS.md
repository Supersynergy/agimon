<!-- REPO-POLISH-AGENTS:START -->
# AGENTS.md

Agimon is a Rust-first developer tool focused on fast local workflows.

## Commands

- `build`: `cargo build --manifest-path core/Cargo.toml`
- `check`: `cargo check --manifest-path core/Cargo.toml`
- `raycast setup`: `cd raycast-extension && bun install`

## Repo Rules

- Optimize for Time-to-First-Success: keep setup and verification commands obvious.
- Keep changes scoped to the domain being edited; avoid catch-all `utils`, `helpers`, and `misc` buckets.
- Preserve existing user changes in this repository. Do not run destructive git commands.
- Add or update tests when behavior changes.
- Put durable architecture rationale in `docs/adr/`.
<!-- REPO-POLISH-AGENTS:END -->

