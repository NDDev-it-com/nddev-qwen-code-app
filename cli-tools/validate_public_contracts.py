#!/usr/bin/env python3
"""Validate nddev-qwen-code-app static public artifacts without side effects."""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
SETUP_IDS = ["nddev-builder"]
PROFILE_POLICY = {
    "full-auto": {"approvalMode": "yolo", "sandbox": False},
    "safe": {"approvalMode": "default", "sandbox": True},
}
MANAGED_FILES = ["settings.json", "QWEN.md", "AGENTS.md", ".claude/CLAUDE.md"]
HOSTS = ["macos-arm64", "macos-x64", "ubuntu-glibc-arm64", "ubuntu-glibc-x64"]
ARCHIVE_DIGESTS = {
    "darwin-arm64": "98b12dd4ffbc41c205b01724d07d502311340cd3c9b2fc5fbf6ca0dbcc0d82b6",
    "darwin-x64": "b7696885bfb1daacbf6433309079121212d0576728745f47f98c3eabe1d5e92e",
    "linux-arm64": "01d664ea21465bf649ce246d8328ed93b88a00d4a87d3db54a4e608b8bbaf454",
    "linux-x64": "30fd2b411c05ec551bcba729862fc41adc0ecbe1492e956d007e3fa38349bb1c",
}
PLACEHOLDER_MARKER = "skele" + "ton"
CANONICAL_INSTRUCTIONS = {
    "AGENTS.md": (
        b"# NDDev Qwen Code Public Module\n"
        b"\n"
        b"This repository owns the public, user-runnable Qwen Code setup manager.\n"
        b"Runtime target instructions installed by the manager are sourced from\n"
        b"`setups/nddev-builder/QWEN.md`; native extension instructions are sourced from\n"
        b"`extensions/nddev-builder/QWEN.md`.\n"
    ),
    ".claude/CLAUDE.md": b"@../AGENTS.md\n",
    "extensions/nddev-builder/QWEN.md": (
        b"# NDDev Builder for Qwen Code\n"
        b"\n"
        b"Use Qwen Code's native extension system. A Qwen extension is a directory with a\n"
        b"`qwen-extension.json` manifest and optional `QWEN.md`, `skills/`, `agents/`,\n"
        b"`commands/`, MCP servers, channels, and extension settings.\n"
        b"\n"
        b"Do not invent a Qwen marketplace manifest for this package. Project Qwen Code\n"
        b"capabilities directly onto the native extension, skill, and subagent surfaces.\n"
    ),
    "setups/nddev-builder/QWEN.md": (
        b"# NDDev Qwen Code Builder Setup\n"
        b"\n"
        b"Work autonomously within the user's stated scope and repository instructions.\n"
        b"The active manager profile selects the runtime approval posture. Keep\n"
        b"destructive operations, secrets, external side effects, and unrelated user\n"
        b"state protected even when approval prompts are not required. Use the NDDev\n"
        b"Builder extension for Qwen-native extension, skill, agent, and settings\n"
        b"authoring guidance when relevant.\n"
    ),
    "setups/nddev-builder/AGENTS.md": (
        b"# NDDev Qwen Code Builder Setup\n"
        b"\n"
        b"Qwen Code reads `AGENTS.md` for cross-agent compatibility. See @QWEN.md for\n"
        b"the authoritative setup instructions.\n"
    ),
    "setups/nddev-builder/.claude/CLAUDE.md": b"@../QWEN.md\n",
}


def read_json(relative: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{relative} must contain a JSON object")
    return value


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)

def require_regular(relative: str, errors: list[str]) -> None:
    path = ROOT / relative
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        errors.append(f"missing required file: {relative}")
        return
    require(stat.S_ISREG(mode), f"required path is not a regular file: {relative}", errors)


def validate_release_closures(errors: list[str]) -> None:
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    required = {
        "README.md",
        "LICENSE",
        "VERSION",
        "AGENTS.md",
        ".claude",
        "build",
        "cli-tools",
        "config",
        "extensions",
        "profiles",
        "references",
        "setups",
    }
    for closure in ("archive_paths", "runtime_paths"):
        match = re.search(rf"(?m)^      {closure}: >-\n((?:        .+\n?)+)", release)
        members = set(match.group(1).split()) if match else set()
        require(required.issubset(members), f"release {closure} membership is incomplete", errors)


def validate_versions(errors: list[str]) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    build = read_json("build/version.json")
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    baseline = read_json("references/qwen-code-baseline.json")
    require(SEMVER.fullmatch(version) is not None, "VERSION is not SemVer", errors)
    require(version != "0.0.0", "VERSION is a placeholder", errors)
    require(build.get("schema_version") == 2, "build schema mismatch", errors)
    require(manifest.get("schema_version") == 2, "manifest schema mismatch", errors)
    require(contract.get("contract_version") == 2, "contract schema mismatch", errors)
    require(build.get("build_version") == version, "build version mismatch", errors)
    require(manifest.get("build_version") == version, "manifest version mismatch", errors)
    require(build.get("python_requires") == ">=3.9", "python requirement mismatch", errors)
    require(
        "qwen_code_release_published_at" not in build,
        "release publication observation must remain private",
        errors,
    )
    require(contract.get("version_ref") == "build/version.json", "version_ref mismatch", errors)
    require(contract.get("manifest_ref") == "build/manifest.json", "manifest_ref mismatch", errors)

    package = baseline.get("package", {})
    require(package.get("name") == build.get("qwen_code_package"), "package mismatch", errors)
    require(package.get("version") == build.get("qwen_code_tested"), "tested version mismatch", errors)
    require(package.get("tarball") == build.get("qwen_code_npm_tarball"), "tarball mismatch", errors)
    require(
        package.get("tarball_size_bytes") == build.get("qwen_code_npm_tarball_size_bytes"),
        "tarball size mismatch",
        errors,
    )
    require(
        package.get("integrity") == build.get("qwen_code_npm_integrity"),
        "npm integrity mismatch",
        errors,
    )
    require(package.get("shasum") == build.get("qwen_code_npm_shasum"), "npm shasum mismatch", errors)
    require(
        baseline.get("standalone_archives") == ARCHIVE_DIGESTS,
        "standalone archive digest catalog mismatch",
        errors,
    )
    release_assets = baseline.get("release_assets", {})
    require(
        set(release_assets) == {f"qwen-code-{platform}.tar.gz" for platform in ARCHIVE_DIGESTS},
        "public release asset catalog must contain only supported runtime archives",
        errors,
    )
    release = baseline.get("release", {})
    require("api_url" not in release, "release API observation must remain private", errors)
    require(
        "published_at" not in release,
        "release publication observation must remain private",
        errors,
    )
    require(
        "target_commitish" not in release,
        "release target commit observation must remain private",
        errors,
    )
    for platform, digest in ARCHIVE_DIGESTS.items():
        asset = release_assets.get(f"qwen-code-{platform}.tar.gz", {})
        require(asset.get("sha256") == digest, f"{platform} archive digest mismatch", errors)
        require(
            isinstance(asset.get("size_bytes"), int) and asset["size_bytes"] > 0,
            f"{platform} archive size missing",
            errors,
        )
    for owner, runtime in (
        ("manifest", manifest.get("runtime_compatibility", {})),
        ("contract", contract.get("runtime_compatibility", {})),
    ):
        require(runtime.get("tested_version") == build.get("qwen_code_tested"), f"{owner} tested version mismatch", errors)
        require(runtime.get("package") == build.get("qwen_code_package"), f"{owner} package mismatch", errors)
        require(runtime.get("baseline_ref") == build.get("runtime_baseline_ref"), f"{owner} baseline mismatch", errors)


def validate_setup_profiles(errors: list[str]) -> None:
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    setup = read_json("setups/nddev-builder/setup.json")
    settings = read_json("setups/nddev-builder/settings.json")
    require(manifest.get("setup_ids") == SETUP_IDS, "manifest setup ids mismatch", errors)
    require(manifest.get("profile_ids") == list(PROFILE_POLICY), "manifest profile ids mismatch", errors)
    require(manifest.get("permission_policy", {}).get("profiles") == PROFILE_POLICY, "permission policy mismatch", errors)
    require(contract.get("setup_system", {}).get("setup_ids") == SETUP_IDS, "contract setup ids mismatch", errors)
    require(contract.get("managed_state", {}).get("managed_files") == MANAGED_FILES, "managed files mismatch", errors)
    require(setup.get("id") == "nddev-builder", "setup id mismatch", errors)
    require(setup.get("managed_files") == MANAGED_FILES, "setup managed files mismatch", errors)
    require(setup.get("builder_extension") == "extensions/nddev-builder", "builder source mismatch", errors)
    require(settings.get("context") == {"fileName": ["QWEN.md"]}, "context file mismatch", errors)
    require("approvalMode" not in settings.get("tools", {}), "setup owns approval mode", errors)
    require("sandbox" not in settings.get("tools", {}), "setup owns sandbox mode", errors)
    for profile_id, expected in PROFILE_POLICY.items():
        profile = read_json(f"profiles/{profile_id}/profile.json")
        require(profile.get("id") == profile_id, f"{profile_id} id mismatch", errors)
        require(
            profile.get("settings_overlay", {}).get("tools") == expected,
            f"{profile_id} permission mapping mismatch",
            errors,
        )


def validate_runtime_metadata(errors: list[str]) -> None:
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    baseline = read_json("references/qwen-code-baseline.json")
    for owner, document in (("manifest", manifest), ("contract", contract)):
        runtime = document.get("runtime_launch", {})
        require(runtime.get("home_environment_variable") == "QWEN_HOME", f"{owner} QWEN_HOME mismatch", errors)
        require(runtime.get("path_inherited") is False, f"{owner} PATH policy mismatch", errors)
        software = document.get("software_install", {})
        require(software.get("supported_hosts") == HOSTS, f"{owner} hosts mismatch", errors)
        require(software.get("vendor_platforms") == list(ARCHIVE_DIGESTS), f"{owner} platforms mismatch", errors)
        require(
            software.get("package_provenance", {}).get("integrity")
            == baseline.get("package", {}).get("integrity"),
            f"{owner} package integrity mismatch",
            errors,
        )
        require(
            software.get("release_archive", {}).get("verification") == "pinned-size-sha256",
            f"{owner} archive verification mismatch",
            errors,
        )


def validate_builder(errors: list[str]) -> None:
    build = read_json("build/version.json")
    contract = read_json("config/nddev-contract.json")
    extension = read_json("extensions/nddev-builder/qwen-extension.json")
    builder = contract.get("builder_extension", {})
    require(extension.get("name") == "nddev-builder", "extension name mismatch", errors)
    require(extension.get("version") == build.get("nddev_builder_extension_version"), "extension version mismatch", errors)
    require(extension.get("contextFileName") == "QWEN.md", "extension context mismatch", errors)
    require(extension.get("skills") == "skills", "extension skills path mismatch", errors)
    require(extension.get("agents") == "agents", "extension agents path mismatch", errors)
    require(builder.get("projection") == "qwen-extension", "builder projection mismatch", errors)
    require(builder.get("default_on") is True, "builder default mismatch", errors)
    require(builder.get("marketplace_manifest") is False, "marketplace policy mismatch", errors)
    for relative in builder.get("native_paths", []):
        require((ROOT / "extensions" / "nddev-builder" / relative).exists(), f"missing extension path: {relative}", errors)


def validate_public_tree(errors: list[str]) -> None:
    for relative, expected in CANONICAL_INSTRUCTIONS.items():
        require_regular(relative, errors)
        path = ROOT / relative
        if path.exists() and not path.is_symlink() and path.is_file():
            require(
                path.read_bytes() == expected,
                f"canonical instruction bytes mismatch: {relative}",
                errors,
            )
    for relative in (".claude", "setups/nddev-builder/.claude"):
        claude = ROOT / relative
        try:
            require(stat.S_ISDIR(claude.lstat().st_mode), f"{relative} is not a real directory", errors)
            require(
                {path.name for path in claude.iterdir()} == {"CLAUDE.md"},
                f"{relative} contains unexpected entries",
                errors,
            )
        except FileNotFoundError:
            errors.append(f"missing required directory: {relative}")
    own_path = Path(__file__).resolve()
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or ".git" in path.parts or path == own_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
        require(PLACEHOLDER_MARKER not in text, f"placeholder found in {path.relative_to(ROOT)}", errors)


def main() -> int:
    errors: list[str] = []
    for check in (
        validate_release_closures,
        validate_versions,
        validate_setup_profiles,
        validate_runtime_metadata,
        validate_builder,
        validate_public_tree,
    ):
        check(errors)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("validate_public_contracts.py: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
