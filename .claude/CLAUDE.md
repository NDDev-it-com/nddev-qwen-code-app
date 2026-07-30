<!--
GENERATED FILE - DO NOT EDIT DIRECTLY
generator: gds
bundle: 0.1.0-dev
source-commit: 97e8bbaa3a0734b156b03bad704503bc46d7575b
input-digest: sha256:c0c45a88a7fccf144937a6eacf980141f5f60c6e03743e0770aa08ddae689568
output-digest: sha256:43772c6fbe817c4a503a4cee536d58e1c95b02f1a5088a7e1821bc7e741bd772
edit-source:
  - .gds/repository.yaml
  - policies/base/repository-default.yaml
  - policies/owners/organization-default.yaml
  - policies/roles/public-module.yaml
  - templates/agents/repository.md.tmpl
  - templates/github-actions/go.yml.tmpl
  - templates/harnesses/claude.md.tmpl
-->
# Claude Code repository contract

## Scope

- GDS repository ID: `repo_01KYFBZ4FSRHBXNZRHAKEHAH7N`.
- Roles: `module`.
- Canonical repository facts: `.gds/repository.yaml`.
- Applied policy bundle: `.gds/bundle.lock.yaml` (`0.1.0-dev`).
- This is a first-class Claude Code projection compiled from the same typed
  inputs as `AGENTS.md`; neither projection is a manual policy source.

## Repository boundaries

- Treat this Git repository as one independent mutation boundary.
- Preserve unrelated dirty changes, branches, worktrees, and submodules.
- Run `gds context --json` before work crosses repository boundaries.
- Do not edit generated projections; change the declared canonical input and
  regenerate.

## Safety

- External writes require explicit approval: `true`.
- Generated projection edits: `forbidden`.
- Private parent context persistence: `forbidden`.
- Visibility: `public`; data: `public`.

## Verification commands

- Test: `python3 -m json.tool config/nddev-contract.json`.

## Claude workflow routing

- Start here: run `gds-orient` (or `gds context --json`) to resolve scope before
  any cross-repository work.
- Active skill profiles: `core, module`. Five profiles exist in total
  (`core`, `estate-admin`, `module`, `device`, `portfolio`); only the listed ones
  are active for this repository. The catalog is `skills/registry.yaml`, and each
  skill lives under `skills/canonical/<name>/SKILL.md`.
- Load procedural detail from the applicable installed GDS skill projection or
  plugin only when the task matches it.
- Destructive workflows remain explicit-only and still require their concrete
  plan and approval gates.
- Treat documentation and Serena memories as derived evidence, never mutation
  authority.

## Done

- Required checks pass or are explicitly reported `NOT_PROVEN`.
- Every affected Git boundary and remote result is classified.
- No secret, private-context leak, unrelated change, or unapproved projection
  drift is introduced.
