# NDDev Qwen Code Setup Manager

`nddev-qwen-code-app` is a dependency-free manager for a caller-selected Qwen
Code home. It installs the `nddev-builder` content setup into an explicit
absolute target, applies a selected runtime profile, and preserves unrelated
authentication, model-provider, MCP, and runtime state.

This build targets the canonical Qwen Code CLI from `QwenLM/qwen-code`, package
identity `@qwen-code/qwen-code`, binary `qwen`, version `0.21.1`.

## Owned state

The setup lifecycle manages:

- `settings.json`
- `QWEN.md`
- `AGENTS.md`
- `.claude/CLAUDE.md`
- `extensions/nddev-builder/`
- `NDDEV-QWEN-CODE-SETUP.json`

The manager never defaults to `~/.qwen`; every mutating command requires
`--target /absolute/path`. Qwen runtime state uses the same explicit target via
`QWEN_HOME=<target>` and `QWEN_RUNTIME_DIR=<target>/runtime`.

## Setup And Profiles

| Profile | Qwen approval mode | Sandbox |
| --- | --- | --- |
| `full-auto` | `yolo` | disabled |
| `safe` | `default` | enabled |

`full-auto` is the default runtime profile. `safe` is a reduced-tool future
profile, not an operating-system security boundary. `balanced` is recognized
only as a legacy managed target state for inspection and migration.

The `nddev-builder` content setup installs the native Qwen extension by
default. The extension uses Qwen Code's native projection:

```text
extensions/nddev-builder/qwen-extension.json
extensions/nddev-builder/QWEN.md
extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md
extensions/nddev-builder/agents/qwen-builder-reviewer.md
```

No Qwen marketplace manifest is shipped because Qwen Code's current native
extension package is the `qwen-extension.json` directory format.

`QWEN.md` is the authoritative managed instruction file. `AGENTS.md` and the
NDDev-managed `.claude/CLAUDE.md` bridge are compatibility pointers to that
authority and do not duplicate setup policy. The upstream baseline records
Qwen Code's observed root `CLAUDE.md` compatibility separately; this manager
does not install that legacy root path for current targets. Current stamps use
schema 2 with explicit
`setup_id=nddev-builder` and `profile_id`; schema-1 `safe` and `full-auto`
targets remain readable for migration, while schema-1 `balanced` targets require
an explicit profile migration.

## Setup lifecycle

```bash
python3 cli-tools/nddev_qwen_code.py list
python3 cli-tools/nddev_qwen_code.py status \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py plan \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py install \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py switch --profile safe \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py restore --backup 0 \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py remove \
  --target /absolute/path/to/qwen-home
```

`plan` is non-mutating. `install` creates a missing target or updates the same
content setup/profile. `switch` is required to change profile identity. Before
changing existing managed state, the manager creates a target-bound backup under
`.<target-name>.nddev-qwen-code-backups/<slot>/`; slots `0` through `9` rotate
oldest-first.

Unmanaged target entries are not deleted. Existing `modelProviders`, `security`,
`model`, `mcpServers`, `env`, and other non-managed settings survive setup
updates and switches when the managed setup keys are intact.

## Target-owned Qwen Code CLI

```bash
python3 cli-tools/nddev_qwen_code.py software-status \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py install-cli \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py update-cli \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py remove-cli \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py launch \
  --target /absolute/path/to/qwen-home \
  --workspace /absolute/path/to/project -- --version
```

`install-cli` downloads the pinned official release archive for the supported
host and verifies its size and SHA-256 before extraction. It also verifies the
matching npm package tarball size, SRI, and SHA-1 provenance with scripts
disabled. The mutable standalone installer is not executed. The manager then
writes a deterministic software manifest and atomically swaps only target-owned
`bin/qwen`, `lib/qwen-code`, and `software/qwen-code.json`.

`install-cli` only accepts an absent software surface. Existing or safe partial
software state must use `update-cli`; unsafe existing paths fail before the
archive is downloaded. `software-status` validates the manifest and bounded
tree digest without executing `bin/qwen`. Launch never resolves `qwen` from
ambient `PATH`; it executes `<target>/bin/qwen` with target-owned
`QWEN_HOME`, `QWEN_RUNTIME_DIR`, `HOME`, `USERPROFILE`, XDG, and temp
directories, and it passes an explicit project cwd from `--workspace` or the
captured caller cwd. Explicit workspaces must be absolute literal paths to
existing accessible non-symlink directories; `~/...` expansion is rejected.
Qwen Code 0.21.1 does not provide a top-level native primary workspace flag for
normal launch. The official `serve --workspace`, `channel pairing ... --cwd`,
normal-launch `--include-directories/--add-dir`, and `--worktree` scope
controls, including yargs camelCase spelling for those multiword options, are
recorded in `references/qwen-code-baseline.json` and blocked when launching
through this manager. Launch forwards ordinary child arguments, but
rejects official Qwen Code arguments that override the managed target,
configuration, extension, MCP, tool, sandbox, authentication, session,
telemetry, or logging scope.

Target-owned software install/update/remove/launch is supported on macOS
arm64/x64 and Ubuntu glibc arm64/x64. Upstream artifact names remain the
official `darwin-*` and `linux-*` assets recorded in
`references/qwen-code-baseline.json`; NDDev does not invent an Ubuntu version
floor.

## Repository Boundary

This public repository contains runtime implementation, setup catalogs, public
contracts, documentation, and public repository automation. Tests, fixtures,
benchmarks, and release validation are external to this public runtime module.

## License

Copyright © 2026 Danil Silantyev / NDDev. Licensed under AGPL-3.0-or-later; see
[LICENSE](LICENSE).
