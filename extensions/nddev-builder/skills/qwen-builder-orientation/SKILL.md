---
name: qwen-builder-orientation
description: Build or review Qwen Code native extensions, skills, subagents, commands, settings, and target-isolated setup systems.
priority: 10
---

# Qwen Builder Orientation

Use this skill when authoring Qwen Code capabilities. Keep artifacts on native
Qwen surfaces:

- `qwen-extension.json` for extension metadata and native path projection.
- `QWEN.md` for extension context.
- `skills/<name>/SKILL.md` for model-invoked skills.
- `agents/<name>.md` or `.yaml` for extension subagents.
- `settings.json` for user configuration such as `tools.approvalMode`,
  `tools.sandbox`, `privacy.usageStatisticsEnabled`, `context.fileName`,
  `modelProviders`, and MCP servers.

Prefer explicit targets through `QWEN_HOME` and `QWEN_RUNTIME_DIR`. Keep
credentials and runtime state outside managed setup files unless the user
explicitly asks for that state to be written.
