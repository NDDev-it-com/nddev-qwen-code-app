#!/usr/bin/env python3
"""Validate nddev-qwen-code-app public contracts without side effects."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
MANAGER_PATH = ROOT / "cli-tools" / "nddev_qwen_code.py"
MANAGER_SPEC = importlib.util.spec_from_file_location("nddev_qwen_code", MANAGER_PATH)
if MANAGER_SPEC is None or MANAGER_SPEC.loader is None:
    raise RuntimeError(f"cannot load {MANAGER_PATH}")
nddev_qwen_code = importlib.util.module_from_spec(MANAGER_SPEC)
sys.modules[MANAGER_SPEC.name] = nddev_qwen_code
MANAGER_SPEC.loader.exec_module(nddev_qwen_code)
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
EXPECTED_MANAGED_FILES = ["settings.json", "QWEN.md", "AGENTS.md", "CLAUDE.md"]
EXPECTED_QWEN = {
    "version": "0.21.1",
    "release_tag": "v0.21.1",
    "release_published_at": "2026-07-28T17:52:26Z",
    "package": "@qwen-code/qwen-code",
    "npm_tarball": "https://registry.npmjs.org/@qwen-code/qwen-code/-/qwen-code-0.21.1.tgz",
    "npm_integrity": "sha512-UTBegRxy3Sy5PbxyVjezHb/pNp24qxrgUnq8V0cNrnlldkvI8iB3/4N3akwhEI3nAFC3Lu1cNPxIV/gIK9L3uw==",
    "npm_shasum": "1d3a8426f6a4ed76ca9cd642e9adc59541973e2d",
    "installer_url": "https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.sh",
    "installer_sha256": "6078a358a75ef3dedfa6014fa1d14984a7da15e84aa34f0077cfec59337e9638",
    "installer_argv": ["--method", "standalone", "--version", "0.21.1", "--no-modify-path"],
    "archive_verification": "official SHA256SUMS",
    "node_requires": ">=22.0.0",
}
EXPECTED_ARCHIVES = {
    "darwin-arm64": "98b12dd4ffbc41c205b01724d07d502311340cd3c9b2fc5fbf6ca0dbcc0d82b6",
    "darwin-x64": "b7696885bfb1daacbf6433309079121212d0576728745f47f98c3eabe1d5e92e",
    "linux-arm64": "01d664ea21465bf649ce246d8328ed93b88a00d4a87d3db54a4e608b8bbaf454",
    "linux-x64": "30fd2b411c05ec551bcba729862fc41adc0ecbe1492e956d007e3fa38349bb1c",
}
EXPECTED_HOSTS = [
    "macos-arm64",
    "macos-x64",
    "ubuntu-glibc-arm64",
    "ubuntu-glibc-x64",
]
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
    require(
        build.get("build_version") == version, "build/version.json build_version mismatch", errors
    )
    require(
        manifest.get("build_version") == version,
        "build/manifest.json build_version mismatch",
        errors,
    )
    require(
        contract.get("version_ref") == "build/version.json", "contract version_ref mismatch", errors
    )
    require(
        contract.get("manifest_ref") == "build/manifest.json",
        "contract manifest_ref mismatch",
        errors,
    )
    require(
        PLACEHOLDER_MARKER not in contract, "contract must not contain placeholder marker", errors
    )
    require(build.get("schema_version") == 2, "build/version.json schema_version mismatch", errors)
    require(
        manifest.get("schema_version") == 2, "build/manifest.json schema_version mismatch", errors
    )
    require(contract.get("contract_version") == 2, "contract_version mismatch", errors)

    require(
        build.get("qwen_code_tested") == EXPECTED_QWEN["version"],
        "tested Qwen version mismatch",
        errors,
    )
    require(
        build.get("qwen_code_release_tag") == EXPECTED_QWEN["release_tag"],
        "Qwen release tag mismatch",
        errors,
    )
    require(
        build.get("qwen_code_release_published_at") == EXPECTED_QWEN["release_published_at"],
        "Qwen release timestamp mismatch",
        errors,
    )
    require(build.get("qwen_code_package") == EXPECTED_QWEN["package"], "package mismatch", errors)
    require(
        build.get("python_requires") == ">=3.9",
        "Python requirement must include macOS system Python 3.9",
        errors,
    )
    require(
        build.get("qwen_code_npm_tarball") == EXPECTED_QWEN["npm_tarball"],
        "npm tarball mismatch",
        errors,
    )
    require(
        build.get("qwen_code_npm_integrity") == EXPECTED_QWEN["npm_integrity"],
        "npm integrity mismatch",
        errors,
    )
    require(
        build.get("qwen_code_npm_shasum") == EXPECTED_QWEN["npm_shasum"],
        "npm shasum mismatch",
        errors,
    )
    require(
        build.get("standalone_installer_url") == nddev_qwen_code.INSTALLER_URL,
        "standalone installer URL mismatch",
        errors,
    )
    require(
        build.get("standalone_installer_sha256") == nddev_qwen_code.INSTALLER_SHA256,
        "standalone installer SHA-256 mismatch",
        errors,
    )
    require(
        build.get("standalone_archive_verification") == EXPECTED_QWEN["archive_verification"],
        "standalone archive verification mismatch",
        errors,
    )
    require(
        build.get("node_requires") == EXPECTED_QWEN["node_requires"],
        "Node requirement mismatch",
        errors,
    )

    for owner, runtime in (
        ("manifest", manifest.get("runtime_compatibility")),
        ("contract", contract.get("runtime_compatibility")),
    ):
        require(isinstance(runtime, dict), f"{owner} runtime_compatibility missing", errors)
        if isinstance(runtime, dict):
            require(
                runtime.get("tested_version") == build.get("qwen_code_tested"),
                f"{owner} tested version mismatch",
                errors,
            )
            require(
                runtime.get("package") == build.get("qwen_code_package"),
                f"{owner} package mismatch",
                errors,
            )
            require(
                runtime.get("release_tag") == build.get("qwen_code_release_tag"),
                f"{owner} release tag mismatch",
                errors,
            )
            require(
                runtime.get("baseline_ref") == build.get("runtime_baseline_ref"),
                f"{owner} baseline ref mismatch",
                errors,
            )
            require(
                runtime.get("version_ref") == "build/version.json",
                f"{owner} version_ref mismatch",
                errors,
            )

    package = baseline.get("package")
    require(isinstance(package, dict), "baseline package block missing", errors)
    if isinstance(package, dict):
        require(
            package.get("name") == build.get("qwen_code_package"),
            "baseline package name mismatch",
            errors,
        )
        require(
            package.get("version") == build.get("qwen_code_tested"),
            "baseline package version mismatch",
            errors,
        )
        require(
            package.get("binary") == "qwen",
            "baseline package binary mismatch",
            errors,
        )
        require(
            package.get("node_requires") == build.get("node_requires"),
            "baseline node requirement mismatch",
            errors,
        )
        require(
            package.get("tarball") == build.get("qwen_code_npm_tarball"),
            "baseline npm tarball mismatch",
            errors,
        )
        require(
            package.get("integrity") == build.get("qwen_code_npm_integrity"),
            "baseline npm integrity mismatch",
            errors,
        )
        require(
            package.get("shasum") == build.get("qwen_code_npm_shasum"),
            "baseline npm shasum mismatch",
            errors,
        )
    installer = baseline.get("standalone_installer")
    require(isinstance(installer, dict), "baseline standalone_installer block missing", errors)
    if isinstance(installer, dict):
        require(
            installer.get("url") == nddev_qwen_code.INSTALLER_URL,
            "baseline installer URL mismatch",
            errors,
        )
        require(
            installer.get("sha256") == nddev_qwen_code.INSTALLER_SHA256,
            "baseline installer SHA-256 mismatch",
            errors,
        )
        require(
            installer.get("argv") == list(nddev_qwen_code.INSTALLER_ARGV),
            "baseline installer argv mismatch",
            errors,
        )
        require(
            installer.get("archive_verification") == EXPECTED_QWEN["archive_verification"],
            "baseline archive verification mismatch",
            errors,
        )
    require(
        baseline.get("standalone_archives") == EXPECTED_ARCHIVES,
        "baseline standalone archive SHA256SUMS mismatch",
        errors,
    )
    release_assets = baseline.get("release_assets")
    require(isinstance(release_assets, dict), "baseline release_assets block missing", errors)
    if isinstance(release_assets, dict):
        for archive, digest in EXPECTED_ARCHIVES.items():
            asset_name = f"qwen-code-{archive}.tar.gz"
            asset = release_assets.get(asset_name)
            require(isinstance(asset, dict), f"missing release asset {asset_name}", errors)
            if isinstance(asset, dict):
                require(
                    asset.get("sha256") == digest,
                    f"release asset {asset_name} digest mismatch",
                    errors,
                )
                require(
                    isinstance(asset.get("size_bytes"), int),
                    f"release asset {asset_name} size missing",
                    errors,
                )
    product_hosts = baseline.get("product_hosts")
    require(isinstance(product_hosts, dict), "baseline product_hosts block missing", errors)
    if isinstance(product_hosts, dict):
        require(
            product_hosts.get("supported") == EXPECTED_HOSTS, "supported host IDs mismatch", errors
        )
        require(
            product_hosts.get("ubuntu_version_floor") is None,
            "Ubuntu version floor must be null",
            errors,
        )


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
        require(
            permission_policy.get("setups") == EXPECTED_SETUP_POLICY,
            "permission setup policy mismatch",
            errors,
        )
        require(
            permission_policy.get("source") == "setups/<id>/settings.json",
            "permission policy source mismatch",
            errors,
        )
    setup_system = contract.get("setup_system")
    require(isinstance(setup_system, dict), "contract setup_system missing", errors)
    if isinstance(setup_system, dict):
        require(
            setup_system.get("setup_ids") == expected_ids, "contract setup_ids mismatch", errors
        )
        require(
            setup_system.get("builder_default_on") is True, "builder must be default-on", errors
        )

    for setup_id, policy in EXPECTED_SETUP_POLICY.items():
        setup = read_json(f"setups/{setup_id}/setup.json")
        settings = read_json(f"setups/{setup_id}/settings.json")
        require(setup.get("id") == setup_id, f"{setup_id} setup id mismatch", errors)
        require(
            setup.get("managed_files") == EXPECTED_MANAGED_FILES,
            f"{setup_id} managed_files mismatch",
            errors,
        )
        require(
            setup.get("builder_extension") == "extensions/nddev-builder",
            f"{setup_id} builder path mismatch",
            errors,
        )
        require(
            setup.get("builder_default_on") is True,
            f"{setup_id} builder must be default-on",
            errors,
        )
        tools = settings.get("tools")
        require(isinstance(tools, dict), f"{setup_id} tools block missing", errors)
        if isinstance(tools, dict):
            require(
                tools.get("approvalMode") == policy["approvalMode"],
                f"{setup_id} approvalMode mismatch",
                errors,
            )
            require(
                tools.get("sandbox") is policy["sandbox"], f"{setup_id} sandbox mismatch", errors
            )
        require(
            settings.get("context") == {"fileName": ["QWEN.md"]},
            f"{setup_id} context file mismatch",
            errors,
        )
        for name in ("QWEN.md", "AGENTS.md", "CLAUDE.md"):
            require(
                (ROOT / "setups" / setup_id / name).is_file(), f"{setup_id} missing {name}", errors
            )
        privacy = settings.get("privacy")
        require(
            isinstance(privacy, dict) and privacy.get("usageStatisticsEnabled") is False,
            f"{setup_id} must disable usage statistics",
            errors,
        )


def validate_runtime_and_software(errors: list[str]) -> None:
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    for owner, surface in (
        ("manifest", manifest.get("runtime_launch")),
        ("contract", contract.get("runtime_launch")),
    ):
        require(isinstance(surface, dict), f"{owner} runtime_launch missing", errors)
        if not isinstance(surface, dict):
            continue
        require(
            surface.get("executable_source")
            == "validated-target-owned-official-standalone-install",
            f"{owner} executable source mismatch",
            errors,
        )
        require(surface.get("path_inherited") is False, f"{owner} must not inherit PATH", errors)
        require(
            surface.get("home_environment_variable") == "QWEN_HOME",
            f"{owner} QWEN_HOME mismatch",
            errors,
        )
        require(
            surface.get("runtime_environment_variable") == "QWEN_RUNTIME_DIR",
            f"{owner} QWEN_RUNTIME_DIR mismatch",
            errors,
        )
    for owner, surface in (
        ("manifest", manifest.get("software_install")),
        ("contract", contract.get("software_install")),
    ):
        require(isinstance(surface, dict), f"{owner} software_install missing", errors)
        if not isinstance(surface, dict):
            continue
        installer = surface.get("installer")
        require(isinstance(installer, dict), f"{owner} installer block missing", errors)
        if isinstance(installer, dict):
            require(
                installer.get("url") == nddev_qwen_code.INSTALLER_URL,
                f"{owner} installer URL mismatch",
                errors,
            )
            require(
                installer.get("sha256") == nddev_qwen_code.INSTALLER_SHA256,
                f"{owner} installer SHA-256 mismatch",
                errors,
            )
            require(
                installer.get("argv") == list(nddev_qwen_code.INSTALLER_ARGV),
                f"{owner} installer argv mismatch",
                errors,
            )
            require(
                installer.get("archive_verification") == "official SHA256SUMS",
                f"{owner} archive verification mismatch",
                errors,
            )
        require(
            surface.get("supported_hosts") == EXPECTED_HOSTS,
            f"{owner} supported host IDs mismatch",
            errors,
        )
        require(
            surface.get("ubuntu_version_floor") is None,
            f"{owner} must not invent an Ubuntu version floor",
            errors,
        )
        require(
            surface.get("vendor_platforms") == list(EXPECTED_ARCHIVES),
            f"{owner} vendor platform mapping mismatch",
            errors,
        )
        bounds = surface.get("bounds")
        require(isinstance(bounds, dict), f"{owner} software bounds missing", errors)
        if isinstance(bounds, dict):
            require(
                bounds.get("max_tree_paths") == nddev_qwen_code.SOFTWARE_TREE_MAX_PATHS,
                f"{owner} software path bound mismatch",
                errors,
            )
            require(
                bounds.get("max_tree_bytes") == nddev_qwen_code.SOFTWARE_TREE_MAX_BYTES,
                f"{owner} software byte bound mismatch",
                errors,
            )
        layout = surface.get("layout")
        require(isinstance(layout, dict), f"{owner} software layout missing", errors)
        if isinstance(layout, dict):
            require(
                layout.get("visible_command") == "bin/qwen",
                f"{owner} visible command mismatch",
                errors,
            )
            require(
                layout.get("entrypoint_resolution") == "target-relative-wrapper",
                f"{owner} entrypoint resolution mismatch",
                errors,
            )
            require(
                layout.get("install_root") == "lib/qwen-code",
                f"{owner} install root mismatch",
                errors,
            )
            require(
                layout.get("software_manifest") == "software/qwen-code.json",
                f"{owner} software manifest mismatch",
                errors,
            )
        preconditions = surface.get("preconditions")
        require(
            isinstance(preconditions, dict),
            f"{owner} software preconditions missing",
            errors,
        )
        if isinstance(preconditions, dict):
            require(
                preconditions.get("install") == "absent target-owned Qwen Code software surface",
                f"{owner} install precondition mismatch",
                errors,
            )
            require(
                preconditions.get("update")
                == "installed or safe partial target-owned Qwen Code software surface",
                f"{owner} update precondition mismatch",
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
    require(
        extension.get("contextFileName") == "QWEN.md", "builder contextFileName mismatch", errors
    )
    require(extension.get("skills") == "skills", "builder skills path mismatch", errors)
    require(extension.get("agents") == "agents", "builder agents path mismatch", errors)
    for relative in (
        "extensions/nddev-builder/QWEN.md",
        "extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md",
        "extensions/nddev-builder/agents/qwen-builder-reviewer.md",
    ):
        require((ROOT / relative).is_file(), f"missing builder native file: {relative}", errors)
    if isinstance(builder, dict):
        require(
            builder.get("projection") == "qwen-extension", "builder projection mismatch", errors
        )
        require(builder.get("default_on") is True, "builder default_on mismatch", errors)
        require(
            builder.get("marketplace_manifest") is False,
            "builder marketplace_manifest must be false",
            errors,
        )
        require(
            builder.get("native_paths") == ["QWEN.md", "skills", "agents"],
            "builder native paths mismatch",
            errors,
        )


def validate_parser_contract(errors: list[str]) -> None:
    require(hasattr(nddev_qwen_code, "parse_args"), "manager must expose parse_args(argv)", errors)
    examples = (
        ["list"],
        ["status", "--target", "/tmp/qwen"],
        ["plan", "--setup", "safe", "--target", "/tmp/qwen"],
        ["install", "--setup", "safe", "--target", "/tmp/qwen"],
        ["switch", "--setup", "balanced", "--target", "/tmp/qwen"],
        ["restore", "--backup", "0", "--target", "/tmp/qwen"],
        ["remove", "--target", "/tmp/qwen"],
        ["builder-status", "--target", "/tmp/qwen"],
        ["software-status", "--target", "/tmp/qwen"],
        ["install-cli", "--target", "/tmp/qwen"],
        ["update-cli", "--target", "/tmp/qwen"],
        ["remove-cli", "--target", "/tmp/qwen"],
        ["launch", "--target", "/tmp/qwen", "--", "--version"],
    )
    for argv in examples:
        try:
            nddev_qwen_code.parse_args(list(argv))
        except SystemExit as exc:
            errors.append(f"parse_args rejected documented argv {argv!r}: {exc.code}")


def validate_public_tree(errors: list[str]) -> None:
    own_path = Path(__file__).resolve()
    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if path.resolve() == own_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        lowered = text.lower()
        if PLACEHOLDER_MARKER in lowered:
            errors.append(f"placeholder marker found in {path.relative_to(ROOT)}")


def main() -> int:
    errors: list[str] = []
    try:
        validate_versions(errors)
        validate_setups(errors)
        validate_runtime_and_software(errors)
        validate_builder(errors)
        validate_parser_contract(errors)
        validate_public_tree(errors)
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
