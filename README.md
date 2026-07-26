# NDDev Qwen Code Setup Manager

`nddev-qwen-code-app` is a dependency-free manager for a caller-selected Qwen
Code home. It installs one of several complete setup variants into an explicit
absolute target and preserves unrelated authentication, model-provider, MCP, and
runtime state.

This build targets the canonical Qwen Code CLI from `QwenLM/qwen-code`, npm
package `@qwen-code/qwen-code`, binary `qwen`, version `0.21.0`.

## Owned state

The setup lifecycle manages:

- `settings.json`
- `QWEN.md`
- `extensions/nddev-builder/`
- `NDDEV-QWEN-CODE-SETUP.json`

The manager never defaults to `~/.qwen`; every mutating command requires
`--target /absolute/path`. Qwen runtime state uses the same explicit target via
`QWEN_HOME=<target>` and `QWEN_RUNTIME_DIR=<target>/runtime`.

## Setups

| Setup | Qwen approval mode | Sandbox |
| --- | --- | --- |
| `safe` | `default` | enabled |
| `balanced` | `auto-edit` | enabled |
| `full-auto` | `yolo` | disabled |

All variants install the native `nddev-builder` Qwen extension by default. The
extension uses Qwen Code's native projection:

```text
extensions/nddev-builder/qwen-extension.json
extensions/nddev-builder/QWEN.md
extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md
extensions/nddev-builder/agents/qwen-builder-reviewer.md
```

No Qwen marketplace manifest is shipped because Qwen Code's current native
extension package is the `qwen-extension.json` directory format.

## Setup lifecycle

```bash
python3 cli-tools/nddev_qwen_code.py list
python3 cli-tools/nddev_qwen_code.py status \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py plan --setup safe \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py install --setup safe \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py switch --setup full-auto \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py restore --backup 0 \
  --target /absolute/path/to/qwen-home
python3 cli-tools/nddev_qwen_code.py remove \
  --target /absolute/path/to/qwen-home
```

`plan` is non-mutating. `install` creates a missing target or updates the same
setup. `switch` is required to change setup identity. Before changing existing
managed state, the manager creates a target-bound backup under
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
python3 cli-tools/nddev_qwen_code.py launch \
  --target /absolute/path/to/qwen-home -- --version
```

`install-cli` and `update-cli` use npm to install
`@qwen-code/qwen-code@0.21.0` below the explicit target, then validate the
target-owned `bin/qwen`. Launch never resolves `qwen` from ambient `PATH`; it
executes `<target>/bin/qwen` with `QWEN_HOME`, `QWEN_RUNTIME_DIR`, `HOME`, and
`USERPROFILE` bound to the selected target.

## Public/private boundary

This public repository contains runtime implementation, setup catalogs, public
contracts, documentation, and public repository automation. Tests, fixtures,
benchmarks, and release validation live in the private `nddev-harnesses`
validation slice.

## License

Copyright © 2026 Danil Silantyev / NDDev. Licensed under AGPL-3.0-or-later; see
[LICENSE](LICENSE).
