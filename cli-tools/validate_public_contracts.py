#!/usr/bin/env python3
"""Validate nddev-qwen-code-app public contracts without side effects."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SEMVER = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
EXPECTED_SETUP_POLICY = {
    "safe": {"approvalMode": "default", "sandbox": True},
    "balanced": {"approvalMode": "auto-edit", "sandbox": True},
    "full-auto": {"approvalMode": "yolo", "sandbox": False},
}
EXPECTED_QWEN = {
    "version": "0.21.0",
    "release_tag": "v0.21.0",
    "release_published_at": "2026-07-24T13:32:48Z",
    "npm_package": "@qwen-code/qwen-code",
    "npm_tarball": "https://registry.npmjs.org/@qwen-code/qwen-code/-/qwen-code-0.21.0.tgz",
    "npm_shasum": "0c34828a81a068ecc07be92c611ae30a644f9bc5",
    "npm_integrity": "sha512-h4t8crH1WTKS4I3uolOQGTzvGu7iW9DuqIegaq+v8yRXTyTkNV7k74AARHPWYh5DJL1ZY/ZCDsOuPsNhaLlnog==",
    "node_requires": ">=22.0.0",
}
OBSOLETE_ALIAS = "q" + "coder"
PLACEHOLDER_MARKER = "skele" + "ton"


def read_json(relative: str) -> dict[str, Any]:
    path = ROOT / relative
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{relative} must contain a JSON object")
    return value


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def validate_versions(errors: list[str]) -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    build = read_json("build/version.json")
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    baseline = read_json("references/qwen-code-baseline.json")

    require(SEMVER.fullmatch(version) is not None, "VERSION is not SemVer", errors)
    require(version != "0.0.0", "VERSION must not be placeholder 0.0.0", errors)
    require(build.get("build_version") == version, "build/version.json build_version mismatch", errors)
    require(manifest.get("build_version") == version, "build/manifest.json build_version mismatch", errors)
    require(contract.get("version_ref") == "build/version.json", "contract version_ref mismatch", errors)
    require(contract.get("manifest_ref") == "build/manifest.json", "contract manifest_ref mismatch", errors)
    require(PLACEHOLDER_MARKER not in contract, "contract must not contain placeholder marker", errors)
    require(build.get("schema_version") == 2, "build/version.json schema_version mismatch", errors)
    require(manifest.get("schema_version") == 2, "build/manifest.json schema_version mismatch", errors)
    require(contract.get("contract_version") == 2, "contract_version mismatch", errors)

    require(build.get("qwen_code_tested") == EXPECTED_QWEN["version"], "tested Qwen version mismatch", errors)
    require(build.get("qwen_code_release_tag") == EXPECTED_QWEN["release_tag"], "Qwen release tag mismatch", errors)
    require(
        build.get("qwen_code_release_published_at") == EXPECTED_QWEN["release_published_at"],
        "Qwen release timestamp mismatch",
        errors,
    )
    require(build.get("npm_package") == EXPECTED_QWEN["npm_package"], "npm package mismatch", errors)
    require(build.get("npm_tarball") == EXPECTED_QWEN["npm_tarball"], "npm tarball mismatch", errors)
    require(build.get("npm_shasum") == EXPECTED_QWEN["npm_shasum"], "npm shasum mismatch", errors)
    require(build.get("npm_integrity") == EXPECTED_QWEN["npm_integrity"], "npm integrity mismatch", errors)
    require(build.get("node_requires") == EXPECTED_QWEN["node_requires"], "Node requirement mismatch", errors)

    for owner, runtime in (
        ("manifest", manifest.get("runtime_compatibility")),
        ("contract", contract.get("runtime_compatibility")),
    ):
        require(isinstance(runtime, dict), f"{owner} runtime_compatibility missing", errors)
        if isinstance(runtime, dict):
            require(runtime.get("tested_version") == build.get("qwen_code_tested"), f"{owner} tested version mismatch", errors)
            require(runtime.get("npm_package") == build.get("npm_package"), f"{owner} npm package mismatch", errors)
            require(runtime.get("release_tag") == build.get("qwen_code_release_tag"), f"{owner} release tag mismatch", errors)
            require(runtime.get("baseline_ref") == build.get("runtime_baseline_ref"), f"{owner} baseline ref mismatch", errors)
            require(runtime.get("version_ref") == "build/version.json", f"{owner} version_ref mismatch", errors)

    npm = baseline.get("npm")
    require(isinstance(npm, dict), "baseline npm block missing", errors)
    if isinstance(npm, dict):
        require(npm.get("package") == build.get("npm_package"), "baseline npm package mismatch", errors)
        require(npm.get("version") == build.get("qwen_code_tested"), "baseline npm version mismatch", errors)
        require(npm.get("tarball") == build.get("npm_tarball"), "baseline npm tarball mismatch", errors)
        require(npm.get("shasum") == build.get("npm_shasum"), "baseline npm shasum mismatch", errors)
        require(npm.get("integrity") == build.get("npm_integrity"), "baseline npm integrity mismatch", errors)
        require(npm.get("node_requires") == build.get("node_requires"), "baseline node requirement mismatch", errors)


def validate_setups(errors: list[str]) -> None:
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    expected_ids = list(EXPECTED_SETUP_POLICY)
    require(manifest.get("setup_ids") == expected_ids, "manifest setup_ids mismatch", errors)
    permission_policy = manifest.get("permission_policy")
    require(isinstance(permission_policy, dict), "manifest permission_policy missing", errors)
    if isinstance(permission_policy, dict):
        require(
            permission_policy.get("configuration_surface") == "tools.approvalMode",
            "permission surface mismatch",
            errors,
        )
        require(permission_policy.get("setups") == EXPECTED_SETUP_POLICY, "permission setup policy mismatch", errors)
        require(permission_policy.get("source") == "setups/<id>/settings.json", "permission policy source mismatch", errors)
    setup_system = contract.get("setup_system")
    require(isinstance(setup_system, dict), "contract setup_system missing", errors)
    if isinstance(setup_system, dict):
        require(setup_system.get("setup_ids") == expected_ids, "contract setup_ids mismatch", errors)
        require(setup_system.get("builder_default_on") is True, "builder must be default-on", errors)

    for setup_id, policy in EXPECTED_SETUP_POLICY.items():
        setup = read_json(f"setups/{setup_id}/setup.json")
        settings = read_json(f"setups/{setup_id}/settings.json")
        require(setup.get("id") == setup_id, f"{setup_id} setup id mismatch", errors)
        require(setup.get("managed_files") == ["settings.json", "QWEN.md"], f"{setup_id} managed_files mismatch", errors)
        require(setup.get("builder_extension") == "extensions/nddev-builder", f"{setup_id} builder path mismatch", errors)
        require(setup.get("builder_default_on") is True, f"{setup_id} builder must be default-on", errors)
        tools = settings.get("tools")
        require(isinstance(tools, dict), f"{setup_id} tools block missing", errors)
        if isinstance(tools, dict):
            require(tools.get("approvalMode") == policy["approvalMode"], f"{setup_id} approvalMode mismatch", errors)
            require(tools.get("sandbox") is policy["sandbox"], f"{setup_id} sandbox mismatch", errors)
        require(settings.get("context") == {"fileName": ["QWEN.md"]}, f"{setup_id} context file mismatch", errors)
        privacy = settings.get("privacy")
        require(
            isinstance(privacy, dict) and privacy.get("usageStatisticsEnabled") is False,
            f"{setup_id} must disable usage statistics",
            errors,
        )


def validate_builder(errors: list[str]) -> None:
    build = read_json("build/version.json")
    contract = read_json("config/nddev-contract.json")
    extension = read_json("extensions/nddev-builder/qwen-extension.json")
    builder = contract.get("builder_extension")
    require(isinstance(builder, dict), "contract builder_extension missing", errors)
    require(extension.get("name") == "nddev-builder", "builder extension name mismatch", errors)
    require(
        extension.get("version") == build.get("nddev_builder_extension_version"),
        "builder extension version mismatch",
        errors,
    )
    require(extension.get("contextFileName") == "QWEN.md", "builder contextFileName mismatch", errors)
    require(extension.get("skills") == "skills", "builder skills path mismatch", errors)
    require(extension.get("agents") == "agents", "builder agents path mismatch", errors)
    for relative in (
        "extensions/nddev-builder/QWEN.md",
        "extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md",
        "extensions/nddev-builder/agents/qwen-builder-reviewer.md",
    ):
        require((ROOT / relative).is_file(), f"missing builder native file: {relative}", errors)
    if isinstance(builder, dict):
        require(builder.get("projection") == "qwen-extension", "builder projection mismatch", errors)
        require(builder.get("default_on") is True, "builder default_on mismatch", errors)
        require(builder.get("marketplace_manifest") is False, "builder marketplace_manifest must be false", errors)
        require(builder.get("native_paths") == ["QWEN.md", "skills", "agents"], "builder native paths mismatch", errors)


def validate_absent_obsolete_aliases(errors: list[str]) -> None:
    own_path = Path(__file__).resolve()
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.resolve() == own_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        if OBSOLETE_ALIAS in lowered:
            errors.append(f"obsolete alias found in {path.relative_to(ROOT)}")
        if PLACEHOLDER_MARKER in lowered:
            errors.append(f"placeholder marker found in {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    try:
        validate_versions(errors)
        validate_setups(errors)
        validate_builder(errors)
        validate_absent_obsolete_aliases(errors)
    except Exception as exc:  # noqa: BLE001 - concise public CLI failure.
        errors.append(str(exc))

    if errors:
        print(f"validate_public_contracts.py: FAIL ({len(errors)} error(s))")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("validate_public_contracts.py: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
