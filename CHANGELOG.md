# Changelog

## Unreleased

## [0.1.1] - 2026-08-05

- Update Qwen Code from `0.21.2` to stable `0.21.5` with exact npm and
  supported standalone-archive identities.
- Re-verify the Node requirement, optional native packages, release assets,
  and launch-scope grammar; upstream reports no breaking changes.

## [0.1.0] - 2026-07-26

- Split Qwen Code content setup identity from runtime approval profiles.
- Keep `balanced` as legacy inspection state while new installs use
  `nddev-builder` with `full-auto` or `safe`.
- Restore recursive cleanup support in the standalone manager.
- Implement the initial target-isolated Qwen Code setup manager.
- Add `safe`, `balanced`, and `full-auto` setup variants.
- Add native `nddev-builder` Qwen extension projection with context, skill, and
  subagent capability.
- Add target-bound backups, rollback, launch, and target-owned CLI status
  surfaces.
