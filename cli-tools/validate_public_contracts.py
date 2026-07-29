#!/usr/bin/env python3
"""Validate nddev-qwen-code-app public contracts without side effects."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
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
EXPECTED_SETUP_IDS = ["nddev-builder"]
EXPECTED_PROFILE_POLICY = {
    "full-auto": {"approvalMode": "yolo", "sandbox": False},
    "safe": {"approvalMode": "default", "sandbox": True},
}
EXPECTED_LEGACY_PROFILE_POLICY = {
    "balanced": {"approvalMode": "auto-edit", "sandbox": True},
}
EXPECTED_MANAGED_FILES = ["settings.json", "QWEN.md", "AGENTS.md", ".claude/CLAUDE.md"]
EXPECTED_BLOCKED_WORKSPACE_CONTROLS = [
    "--include-directories",
    "--add-dir",
    "--includeDirectories",
    "--addDir",
    "--worktree",
    "--workspace",
    "--cwd",
]
EXPECTED_BLOCKED_SHORT_VALUE_FORMS = ["-e", "-i", "-m", "-o", "-p", "-r"]
EXPECTED_QWEN = {
    "version": "0.21.1",
    "release_tag": "v0.21.1",
    "release_published_at": "2026-07-28T17:52:26Z",
    "package": "@qwen-code/qwen-code",
    "npm_tarball": "https://registry.npmjs.org/@qwen-code/qwen-code/-/qwen-code-0.21.1.tgz",
    "npm_tarball_size_bytes": 23836955,
    "npm_integrity": "sha512-UTBegRxy3Sy5PbxyVjezHb/pNp24qxrgUnq8V0cNrnlldkvI8iB3/4N3akwhEI3nAFC3Lu1cNPxIV/gIK9L3uw==",
    "npm_shasum": "1d3a8426f6a4ed76ca9cd642e9adc59541973e2d",
    "release_base_url": "https://github.com/QwenLM/qwen-code/releases/download/v0.21.1",
    "archive_verification": "pinned-size-sha256",
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


def file_tree_snapshot(root: Path) -> list[tuple[Any, ...]]:
    if not root.exists():
        return []
    rows: list[tuple[Any, ...]] = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item.relative_to(root))):
        relative = str(path.relative_to(root))
        info = path.lstat()
        mode = info.st_mode
        payload: bytes | str | None = None
        if os.path.islink(path):
            payload = os.readlink(path)
        elif path.is_file():
            payload = path.read_bytes()
        rows.append(
            (
                relative,
                mode,
                getattr(info, "st_uid", None),
                info.st_nlink,
                info.st_dev,
                info.st_ino,
                info.st_size,
                info.st_mtime_ns,
                payload,
            )
        )
    return rows


def make_private_dir(path: Path) -> None:
    path.mkdir(parents=True, mode=nddev_qwen_code.OWNER_DIRECTORY_MODE)
    path.chmod(nddev_qwen_code.OWNER_DIRECTORY_MODE)


def write_private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(nddev_qwen_code.OWNER_FILE_MODE)


def assert_anchor_failure_unchanged(
    action: Any,
    root: Path,
    label: str,
    errors: list[str],
) -> None:
    before = file_tree_snapshot(root)
    try:
        action()
    except nddev_qwen_code.QwenCodeSetupError:
        pass
    else:
        errors.append(f"{label} unexpectedly succeeded")
        return
    after = file_tree_snapshot(root)
    require(before == after, f"{label} mutated the coordination namespace", errors)


def acquire_anchor_once(anchor: Path, kind: str, target: Path | None) -> None:
    with nddev_qwen_code.anchor_lock(
        anchor,
        kind=kind,
        target=target,
        exclusive=True,
        create=True,
    ):
        pass


def replace_empty_directory_at_path(path: Path) -> tuple[tuple[int, int], tuple[Any, ...]]:
    parent = path.parent
    replacement = parent / f".{path.name}.replacement"
    saved = parent / f".{path.name}.saved"
    path.rename(saved)
    nddev_qwen_code.fsync_directory(parent)
    saved.rmdir()
    nddev_qwen_code.fsync_directory(parent)
    replacement.mkdir(mode=nddev_qwen_code.OWNER_DIRECTORY_MODE)
    replacement.chmod(nddev_qwen_code.OWNER_DIRECTORY_MODE)
    nddev_qwen_code.fsync_directory(parent)
    replacement.rename(path)
    nddev_qwen_code.fsync_directory(parent)
    replacement_info = path.lstat()
    parent_info = parent.lstat()
    return nddev_qwen_code.identity_of(replacement_info), nddev_qwen_code.namespace_identity(
        parent_info
    )


def assert_created_directory_replacement_survives(
    signature: Any,
    label: str,
    errors: list[str],
) -> None:
    replacement_identity, parent_after_replace = replace_empty_directory_at_path(signature.path)
    try:
        nddev_qwen_code.rollback_created_directory(signature, label)
    except nddev_qwen_code.QwenCodeSetupError:
        pass
    else:
        errors.append(f"{label} rollback accepted a same-path replacement")
        return
    current = signature.path.lstat()
    require(
        nddev_qwen_code.identity_of(current) == replacement_identity,
        f"{label} rollback removed or replaced the same-path replacement",
        errors,
    )
    require(
        nddev_qwen_code.namespace_identity(signature.path.parent.lstat()) == parent_after_replace,
        f"{label} rollback restored parent metadata after replacement",
        errors,
    )


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
        build.get("qwen_code_npm_tarball_size_bytes") == EXPECTED_QWEN["npm_tarball_size_bytes"],
        "npm tarball size mismatch",
        errors,
    )
    require(
        build.get("qwen_code_npm_shasum") == EXPECTED_QWEN["npm_shasum"],
        "npm shasum mismatch",
        errors,
    )
    require(
        build.get("qwen_code_release_base_url") == nddev_qwen_code.QWEN_RELEASE_BASE_URL,
        "release archive base URL mismatch",
        errors,
    )
    require(
        build.get("qwen_code_release_archive_verification")
        == EXPECTED_QWEN["archive_verification"],
        "release archive verification mismatch",
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
            package.get("tarball_size_bytes") == build.get("qwen_code_npm_tarball_size_bytes"),
            "baseline npm tarball size mismatch",
            errors,
        )
        require(
            package.get("shasum") == build.get("qwen_code_npm_shasum"),
            "baseline npm shasum mismatch",
            errors,
        )
    installer_observation = baseline.get("mutable_installer_observation")
    require(
        isinstance(installer_observation, dict),
        "baseline mutable installer observation block missing",
        errors,
    )
    if isinstance(installer_observation, dict):
        require(
            installer_observation.get("trusted_for_install") is False,
            "mutable installer must not be trusted for install",
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
        require(
            nddev_qwen_code.QWEN_RELEASE_ARCHIVES
            == {
                archive: {
                    "asset": f"qwen-code-{archive}.tar.gz",
                    "size_bytes": release_assets[f"qwen-code-{archive}.tar.gz"]["size_bytes"],
                    "sha256": digest,
                }
                for archive, digest in EXPECTED_ARCHIVES.items()
            },
            "manager release archive catalog mismatch",
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
    configuration = baseline.get("configuration")
    require(isinstance(configuration, dict), "baseline configuration block missing", errors)
    if isinstance(configuration, dict):
        require(
            configuration.get("global_context_file") == "QWEN.md",
            "baseline Qwen context file mismatch",
            errors,
        )
        require(
            configuration.get("agents_compatibility_file") == "AGENTS.md",
            "baseline AGENTS compatibility file mismatch",
            errors,
        )
        require(
            configuration.get("qwen_native_claude_compatibility_observation") == "CLAUDE.md",
            "baseline native Claude observation mismatch",
            errors,
        )
        require(
            configuration.get("nddev_managed_claude_bridge") == ".claude/CLAUDE.md",
            "baseline managed Claude bridge mismatch",
            errors,
        )
    grammar = baseline.get("launch_scope_grammar")
    require(isinstance(grammar, dict), "baseline launch_scope_grammar missing", errors)
    if isinstance(grammar, dict):
        require(
            grammar.get("normal_launch_primary_workspace_flag") is None,
            "baseline must not invent native primary workspace flag",
            errors,
        )
        require(
            grammar.get("normal_launch_cwd_flag") is None,
            "baseline normal launch cwd flag mismatch",
            errors,
        )
        require(
            grammar.get("normal_launch_project_flag") is None,
            "baseline normal launch project flag mismatch",
            errors,
        )
        require(
            grammar.get("blocked_workspace_controls") == EXPECTED_BLOCKED_WORKSPACE_CONTROLS,
            "baseline blocked workspace controls mismatch",
            errors,
        )
        require(
            grammar.get("blocked_short_value_forms") == EXPECTED_BLOCKED_SHORT_VALUE_FORMS,
            "baseline blocked short value forms mismatch",
            errors,
        )
        require(
            grammar.get("assignment_forms_blocked") is True,
            "baseline assignment forms policy mismatch",
            errors,
        )
        require(
            grammar.get("attached_short_value_forms_blocked") is True,
            "baseline attached short forms policy mismatch",
            errors,
        )


def validate_setups(errors: list[str]) -> None:
    manifest = read_json("build/manifest.json")
    contract = read_json("config/nddev-contract.json")
    expected_profiles = list(EXPECTED_PROFILE_POLICY)
    expected_legacy = ["safe", "balanced", "full-auto"]
    require(manifest.get("setup_ids") == EXPECTED_SETUP_IDS, "manifest setup_ids mismatch", errors)
    require(
        manifest.get("profile_ids") == expected_profiles, "manifest profile_ids mismatch", errors
    )
    require(
        manifest.get("default_profile") == "full-auto",
        "manifest default_profile mismatch",
        errors,
    )
    require(
        manifest.get("legacy_setup_ids") == expected_legacy,
        "manifest legacy setup ids mismatch",
        errors,
    )
    permission_policy = manifest.get("permission_policy")
    require(isinstance(permission_policy, dict), "manifest permission_policy missing", errors)
    if isinstance(permission_policy, dict):
        require(
            permission_policy.get("configuration_surface")
            == "profiles/<profile-id>/profile.json:settings_overlay.tools",
            "permission surface mismatch",
            errors,
        )
        require(
            permission_policy.get("profiles") == EXPECTED_PROFILE_POLICY,
            "permission profile policy mismatch",
            errors,
        )
        require(
            permission_policy.get("legacy_profiles") == EXPECTED_LEGACY_PROFILE_POLICY,
            "permission legacy profile policy mismatch",
            errors,
        )
        require(
            permission_policy.get("source") == "profiles/<id>/profile.json",
            "permission policy source mismatch",
            errors,
        )
    setup_system = contract.get("setup_system")
    require(isinstance(setup_system, dict), "contract setup_system missing", errors)
    if isinstance(setup_system, dict):
        require(
            setup_system.get("setup_ids") == EXPECTED_SETUP_IDS,
            "contract setup_ids mismatch",
            errors,
        )
        require(
            setup_system.get("profile_ids") == expected_profiles,
            "contract profile_ids mismatch",
            errors,
        )
        require(
            setup_system.get("default_profile") == "full-auto",
            "contract default_profile mismatch",
            errors,
        )
        require(
            setup_system.get("legacy_setup_ids") == expected_legacy,
            "contract legacy setup ids mismatch",
            errors,
        )
        require(
            setup_system.get("builder_default_on") is True, "builder must be default-on", errors
        )
    managed_state = contract.get("managed_state")
    require(isinstance(managed_state, dict), "contract managed_state missing", errors)
    if isinstance(managed_state, dict):
        require(managed_state.get("stamp_schema") == 2, "contract stamp_schema mismatch", errors)
        require(
            managed_state.get("managed_files") == EXPECTED_MANAGED_FILES,
            "contract managed_files mismatch",
            errors,
        )
        legacy = managed_state.get("legacy_stamp_schema")
        require(isinstance(legacy, dict), "contract legacy stamp schema missing", errors)
        if isinstance(legacy, dict):
            require(
                legacy.get("readable_setup_ids") == ["safe", "full-auto"],
                "contract legacy readable setup ids mismatch",
                errors,
            )
            require(
                legacy.get("migration_required_setup_ids") == ["balanced"],
                "contract legacy migration setup ids mismatch",
                errors,
            )

    for setup_id in EXPECTED_SETUP_IDS:
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
        require(
            setup.get("default_profile") == "full-auto",
            f"{setup_id} default_profile mismatch",
            errors,
        )
        require(
            setup.get("profiles") == expected_profiles,
            f"{setup_id} profile list mismatch",
            errors,
        )
        tools = settings.get("tools")
        require(isinstance(tools, dict), f"{setup_id} tools block missing", errors)
        if isinstance(tools, dict):
            require(
                "approvalMode" not in tools,
                f"{setup_id} must not own approvalMode directly",
                errors,
            )
            require(
                "sandbox" not in tools,
                f"{setup_id} must not own sandbox directly",
                errors,
            )
        require(
            settings.get("context") == {"fileName": ["QWEN.md"]},
            f"{setup_id} context file mismatch",
            errors,
        )
        for name in ("QWEN.md", "AGENTS.md", ".claude/CLAUDE.md"):
            require(
                (ROOT / "setups" / setup_id / name).is_file(), f"{setup_id} missing {name}", errors
            )
        privacy = settings.get("privacy")
        require(
            isinstance(privacy, dict) and privacy.get("usageStatisticsEnabled") is False,
            f"{setup_id} must disable usage statistics",
            errors,
        )
    for profile_id, policy in EXPECTED_PROFILE_POLICY.items():
        profile = read_json(f"profiles/{profile_id}/profile.json")
        require(profile.get("id") == profile_id, f"{profile_id} profile id mismatch", errors)
        overlay = profile.get("settings_overlay")
        require(isinstance(overlay, dict), f"{profile_id} settings_overlay missing", errors)
        tools = overlay.get("tools") if isinstance(overlay, dict) else None
        require(isinstance(tools, dict), f"{profile_id} tools overlay missing", errors)
        if isinstance(tools, dict):
            require(
                tools.get("approvalMode") == policy["approvalMode"],
                f"{profile_id} approvalMode mismatch",
                errors,
            )
            require(
                tools.get("sandbox") is policy["sandbox"],
                f"{profile_id} sandbox mismatch",
                errors,
            )
    stamp_policy = manifest.get("stamp_policy")
    require(isinstance(stamp_policy, dict), "manifest stamp_policy missing", errors)
    if isinstance(stamp_policy, dict):
        require(stamp_policy.get("schema_version") == 2, "manifest stamp schema mismatch", errors)
        require(
            stamp_policy.get("setup_id") == "nddev-builder",
            "manifest stamp setup mismatch",
            errors,
        )
        require(
            stamp_policy.get("profile_field") == "profile_id",
            "manifest stamp profile field mismatch",
            errors,
        )
        require(
            stamp_policy.get("builder_projection_field") == "builder_projection",
            "manifest stamp builder projection field mismatch",
            errors,
        )

    _, rendered = nddev_qwen_code.render_setup("nddev-builder", "safe")
    stamp = json.loads(
        nddev_qwen_code.stamp_bytes(Path("/tmp/qwen"), "nddev-builder", "safe", rendered).decode(
            "utf-8"
        )
    )
    require(stamp.get("schema_version") == 2, "manager stamp schema mismatch", errors)
    require(stamp.get("setup_id") == "nddev-builder", "manager stamp setup mismatch", errors)
    require(stamp.get("profile_id") == "safe", "manager stamp profile mismatch", errors)
    require(
        stamp.get("builder_projection") == nddev_qwen_code.BUILDER_PROJECTION,
        "manager stamp builder projection mismatch",
        errors,
    )
    require(
        sorted(stamp.get("managed_paths", {})) == sorted(nddev_qwen_code.CURRENT_PAYLOAD_PATHS),
        "manager stamp managed paths mismatch",
        errors,
    )
    legacy_paths = {name: "0" * 64 for name in nddev_qwen_code.LEGACY_SCHEMA1_PAYLOAD_PATHS}
    for setup_id, expected_profile, migration_required in (
        ("safe", "safe", False),
        ("full-auto", "full-auto", False),
        ("balanced", None, True),
    ):
        legacy_stamp = {
            "schema_version": 1,
            "product_name": nddev_qwen_code.PRODUCT_NAME,
            "build_version": "0.1.0",
            "setup_id": setup_id,
            "canonical_target": "/tmp/qwen",
            "managed_paths": legacy_paths,
        }
        normalized = nddev_qwen_code.normalize_stamp(legacy_stamp, Path("/tmp/qwen"))
        require(
            normalized["_content_setup_id"] == "nddev-builder",
            f"legacy {setup_id} content setup normalization mismatch",
            errors,
        )
        require(
            normalized["_profile_id"] == expected_profile,
            f"legacy {setup_id} profile normalization mismatch",
            errors,
        )
        require(
            normalized["_migration_required"] is migration_required,
            f"legacy {setup_id} migration flag mismatch",
            errors,
        )
    legacy_backup_records = {
        name: {"path": name, "size": 0, "sha256": "0" * 64}
        for name in nddev_qwen_code.LEGACY_SCHEMA1_PAYLOAD_PATHS
    }
    require(
        sorted(
            nddev_qwen_code.validate_backup_record_map(
                legacy_backup_records,
                "legacy backup",
            )
        )
        == sorted(nddev_qwen_code.LEGACY_SCHEMA1_PAYLOAD_PATHS),
        "legacy backup payload map was not accepted",
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
            surface.get("executable_source") == "validated-target-owned-official-release-archive",
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
        workspace_scope = surface.get("workspace_scope")
        require(isinstance(workspace_scope, dict), f"{owner} workspace_scope missing", errors)
        if isinstance(workspace_scope, dict):
            require(
                workspace_scope.get("manager_option")
                == "--workspace <absolute-existing-directory>",
                f"{owner} workspace manager option mismatch",
                errors,
            )
            require(
                workspace_scope.get("default") == "captured caller cwd",
                f"{owner} workspace default mismatch",
                errors,
            )
            require(
                workspace_scope.get("native_qwen_primary_workspace_flag") is None,
                f"{owner} must not invent a native primary workspace flag",
                errors,
            )
            require(
                workspace_scope.get("child_cwd_explicit") is True,
                f"{owner} child cwd must be explicit",
                errors,
            )
            require(
                workspace_scope.get("blocked_native_workspace_controls")
                == EXPECTED_BLOCKED_WORKSPACE_CONTROLS,
                f"{owner} blocked workspace controls mismatch",
                errors,
            )
            require(
                workspace_scope.get("blocked_short_value_forms")
                == EXPECTED_BLOCKED_SHORT_VALUE_FORMS,
                f"{owner} blocked short value forms mismatch",
                errors,
            )
            require(
                workspace_scope.get("grammar_source_ref")
                == "references/qwen-code-baseline.json:launch_scope_grammar",
                f"{owner} launch grammar source ref mismatch",
                errors,
            )
    for owner, surface in (
        ("manifest", manifest.get("software_install")),
        ("contract", contract.get("software_install")),
    ):
        require(isinstance(surface, dict), f"{owner} software_install missing", errors)
        if not isinstance(surface, dict):
            continue
        package_provenance = surface.get("package_provenance")
        require(
            isinstance(package_provenance, dict),
            f"{owner} package_provenance block missing",
            errors,
        )
        if isinstance(package_provenance, dict):
            require(
                package_provenance.get("tarball") == nddev_qwen_code.QWEN_NPM_TARBALL_URL,
                f"{owner} npm tarball mismatch",
                errors,
            )
            require(
                package_provenance.get("tarball_size_bytes")
                == nddev_qwen_code.QWEN_NPM_TARBALL_SIZE_BYTES,
                f"{owner} npm tarball size mismatch",
                errors,
            )
            require(
                package_provenance.get("integrity") == nddev_qwen_code.QWEN_NPM_INTEGRITY,
                f"{owner} npm integrity mismatch",
                errors,
            )
            require(
                package_provenance.get("shasum") == nddev_qwen_code.QWEN_NPM_SHASUM,
                f"{owner} npm shasum mismatch",
                errors,
            )
            require(
                package_provenance.get("scripts_enabled") is False,
                f"{owner} install scripts must be disabled",
                errors,
            )
        release_archive = surface.get("release_archive")
        require(
            isinstance(release_archive, dict),
            f"{owner} release_archive block missing",
            errors,
        )
        if isinstance(release_archive, dict):
            require(
                release_archive.get("base_url") == nddev_qwen_code.QWEN_RELEASE_BASE_URL,
                f"{owner} release archive base URL mismatch",
                errors,
            )
            require(
                release_archive.get("verification") == EXPECTED_QWEN["archive_verification"],
                f"{owner} release archive verification mismatch",
                errors,
            )
            require(
                release_archive.get("materialization") == "manager-owned-safe-extract-no-scripts",
                f"{owner} release archive materialization mismatch",
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

    def expect_workspace_rejection(raw: str | None, caller: str | None, label: str) -> None:
        try:
            nddev_qwen_code.resolve_launch_workspace(raw, caller)
        except nddev_qwen_code.QwenCodeSetupError:
            return
        errors.append(f"launch workspace validation accepted {label}")

    examples = (
        ["list"],
        ["status", "--target", "/tmp/qwen"],
        ["plan", "--target", "/tmp/qwen"],
        ["install", "--target", "/tmp/qwen"],
        ["install", "--setup", "safe", "--target", "/tmp/qwen"],
        ["install", "--setup", "full-auto", "--target", "/tmp/qwen"],
        ["switch", "--setup", "nddev-builder", "--profile", "safe", "--target", "/tmp/qwen"],
        ["restore", "--backup", "0", "--target", "/tmp/qwen"],
        ["remove", "--target", "/tmp/qwen"],
        ["builder-status", "--target", "/tmp/qwen"],
        ["software-status", "--target", "/tmp/qwen"],
        ["install-cli", "--target", "/tmp/qwen"],
        ["update-cli", "--target", "/tmp/qwen"],
        ["remove-cli", "--target", "/tmp/qwen"],
        ["launch", "--target", "/tmp/qwen", "--", "--version"],
        ["launch", "--target", "/tmp/qwen", "--workspace", str(ROOT), "--", "--version"],
    )
    for argv in examples:
        try:
            nddev_qwen_code.parse_args(list(argv))
        except SystemExit as exc:
            errors.append(f"parse_args rejected documented argv {argv!r}: {exc.code}")
    parsed_launch = nddev_qwen_code.parse_args(
        ["launch", "--target", "/tmp/qwen", "--workspace", str(ROOT), "--", "--version"]
    )
    try:
        workspace = nddev_qwen_code.resolve_launch_workspace(parsed_launch.workspace, str(ROOT))
    except nddev_qwen_code.QwenCodeSetupError as exc:
        errors.append(f"launch workspace validation rejected repository root: {exc}")
    else:
        require(workspace == ROOT, "launch workspace resolution mismatch", errors)
    try:
        captured = nddev_qwen_code.resolve_launch_workspace(None, str(ROOT.resolve(strict=True)))
    except nddev_qwen_code.QwenCodeSetupError as exc:
        errors.append(f"launch captured cwd validation rejected repository root: {exc}")
    else:
        require(captured == ROOT.resolve(strict=True), "captured cwd resolution mismatch", errors)

    original_capture = nddev_qwen_code.capture_caller_cwd
    original_run = nddev_qwen_code.run
    try:
        explicit_capture_calls: list[str] = []
        explicit_seen: list[tuple[str | None, str | None]] = []

        def failing_capture() -> str:
            explicit_capture_calls.append("capture")
            raise AssertionError("explicit workspace must not capture ambient cwd")

        def record_explicit_run(args: Any) -> int:
            explicit_seen.append((args.workspace, getattr(args, "caller_cwd", None)))
            return 0

        nddev_qwen_code.capture_caller_cwd = failing_capture
        nddev_qwen_code.run = record_explicit_run
        explicit_rc = nddev_qwen_code.main(
            ["launch", "--target", "/tmp/qwen", "--workspace", str(ROOT), "--", "--version"]
        )
        require(explicit_rc == 0, "explicit workspace launch smoke rc mismatch", errors)
        require(
            explicit_capture_calls == [],
            "explicit workspace launch captured ambient cwd",
            errors,
        )
        require(
            explicit_seen == [(str(ROOT), None)],
            "explicit workspace launch smoke argument mismatch",
            errors,
        )

        default_capture_calls: list[str] = []
        default_seen: list[tuple[str | None, str | None]] = []

        def counting_capture() -> str:
            default_capture_calls.append("capture")
            return str(ROOT)

        def record_default_run(args: Any) -> int:
            default_seen.append((args.workspace, getattr(args, "caller_cwd", None)))
            return 0

        nddev_qwen_code.capture_caller_cwd = counting_capture
        nddev_qwen_code.run = record_default_run
        default_rc = nddev_qwen_code.main(["launch", "--target", "/tmp/qwen", "--", "--version"])
        require(default_rc == 0, "default workspace launch smoke rc mismatch", errors)
        require(
            default_capture_calls == ["capture"],
            "default workspace launch did not capture cwd exactly once",
            errors,
        )
        require(
            default_seen == [(None, str(ROOT))],
            "default workspace launch smoke argument mismatch",
            errors,
        )
    except AssertionError as exc:
        errors.append(str(exc))
    finally:
        nddev_qwen_code.capture_caller_cwd = original_capture
        nddev_qwen_code.run = original_run

    expect_workspace_rejection(str(ROOT / ".nddev-qwen-missing-workspace"), None, "missing path")
    expect_workspace_rejection(str(ROOT / "README.md"), None, "regular file")
    expect_workspace_rejection("~/nddev-qwen", None, "tilde path")
    with tempfile.TemporaryDirectory(prefix="nddev-qwen-workspace-") as temp_root:
        temp = Path(temp_root)
        real_dir = temp / "real"
        real_dir.mkdir()
        symlink_dir = temp / "link"
        os.symlink(real_dir, symlink_dir)
        expect_workspace_rejection(str(symlink_dir), None, "symlink directory")
        inaccessible = temp / "inaccessible"
        inaccessible.mkdir()
        inaccessible.chmod(0)
        try:
            if not nddev_qwen_code.user_access(inaccessible, os.R_OK | os.X_OK):
                expect_workspace_rejection(str(inaccessible), None, "inaccessible directory")
        finally:
            inaccessible.chmod(0o700)
    for argv, expected in (
        (["--include-directories=/tmp/project"], "--include-directories"),
        (["--add-dir", "/tmp/project"], "--add-dir"),
        (["--includeDirectories=/tmp/project"], "--includeDirectories"),
        (["--addDir", "/tmp/project"], "--addDir"),
        (["--worktree=feature"], "--worktree"),
        (["serve", "--workspace=/tmp/project"], "--workspace"),
        (["channel", "pairing", "list", "bot", "--cwd=/tmp/project"], "--cwd"),
        (["-mtest-model"], "-m"),
        (["-eextension"], "-e"),
        (["-r123"], "-r"),
        (["-phello"], "-p"),
        (["-ihello"], "-i"),
        (["-ojson"], "-o"),
    ):
        require(
            nddev_qwen_code.first_qwen_scope_override(list(argv)) == expected,
            f"launch scope override detection mismatch for {argv!r}",
            errors,
        )
    for setup_id, expected_profile in (("safe", "safe"), ("full-auto", "full-auto")):
        try:
            parsed = nddev_qwen_code.parse_args(
                ["install", "--setup", setup_id, "--target", "/tmp/qwen"]
            )
            setup, profile = nddev_qwen_code.resolve_setup_profile(parsed.setup, parsed.profile)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 - report validator context.
            errors.append(f"legacy setup {setup_id} did not resolve cleanly: {exc}")
            continue
        require(setup == "nddev-builder", f"legacy setup {setup_id} setup mismatch", errors)
        require(
            profile == expected_profile,
            f"legacy setup {setup_id} profile mismatch",
            errors,
        )
    for argv in (
        ["install", "--setup", "safe", "--profile", "full-auto", "--target", "/tmp/qwen"],
        ["install", "--setup", "balanced", "--target", "/tmp/qwen"],
    ):
        try:
            parsed = nddev_qwen_code.parse_args(argv)
            nddev_qwen_code.resolve_setup_profile(parsed.setup, parsed.profile)
        except nddev_qwen_code.QwenCodeSetupError:
            continue
        except SystemExit as exc:
            errors.append(f"parse_args unexpectedly rejected {argv!r}: {exc.code}")
            continue
        errors.append(f"legacy setup compatibility accepted invalid argv {argv!r}")


def stage_kill_script(anchor: Path, kind: str, target: Path | None) -> str:
    target_expr = "None" if target is None else f"Path({str(target)!r})"
    return f"""
import importlib.util
import os
import signal
import sys
from pathlib import Path
manager_path = Path({str(MANAGER_PATH)!r})
spec = importlib.util.spec_from_file_location("nddev_qwen_code_stage_kill", manager_path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
anchor = Path({str(anchor)!r})
target = {target_expr}
payload = module.anchor_payload({kind!r}, target=target)
module.prepare_anchor_publication_stage(anchor, payload, kind={kind!r}, target=target)
os.kill(os.getpid(), signal.SIGKILL)
"""


def assert_sigkill_stage_recovery(
    root: Path,
    anchor: Path,
    kind: str,
    target: Path | None,
    errors: list[str],
) -> None:
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPYCACHEPREFIX": str(root.parent / "pycache"),
    }
    system_python = Path("/usr/bin/python3")
    python = str(system_python if system_python.exists() else Path(sys.executable))
    completed = subprocess.run(
        [python, "-B", "-c", stage_kill_script(anchor, kind, target)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=10,
        check=False,
    )
    require(
        completed.returncode == -signal.SIGKILL,
        f"{kind} stage child did not die by SIGKILL: {completed.returncode}",
        errors,
    )
    require(not anchor.exists(), f"{kind} final anchor appeared before link", errors)
    payload = nddev_qwen_code.anchor_payload(kind, target=target)
    try:
        stages = nddev_qwen_code.valid_anchor_publication_stages(
            anchor,
            payload,
            kind=kind,
            target=target,
        )
    except nddev_qwen_code.QwenCodeSetupError as exc:
        errors.append(f"{kind} SIGKILL stage did not validate: {exc}")
        return
    require(len(stages) == 1, f"{kind} SIGKILL stage count mismatch", errors)
    before = file_tree_snapshot(root)
    with nddev_qwen_code.anchor_lock(anchor, kind=kind, target=target, exclusive=True, create=True):
        pass
    after = file_tree_snapshot(root)
    final = anchor.lstat()
    require(anchor.exists(), f"{kind} final anchor was not promoted", errors)
    require(final.st_nlink == 1, f"{kind} final anchor did not converge to nlink=1", errors)
    require(
        not any(row[0].endswith(".tmp") for row in after),
        f"{kind} publication stage residue remained after recovery",
        errors,
    )
    require(before != after, f"{kind} recovery did not mutate expected namespace", errors)


def validate_anchor_publication_contract(errors: list[str]) -> None:
    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-public-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        make_private_dir(root)
        product = nddev_qwen_code.product_anchor_path(root)
        assert_sigkill_stage_recovery(root, product, "product", None, errors)

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-target-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        targets = root / nddev_qwen_code.TARGET_ANCHOR_DIRECTORY
        make_private_dir(targets)
        target = temp / "target"
        anchor = nddev_qwen_code.target_anchor_path(root, target)
        assert_sigkill_stage_recovery(root, anchor, "target", target, errors)

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-multi-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        make_private_dir(root)
        anchor = nddev_qwen_code.product_anchor_path(root)
        payload = nddev_qwen_code.anchor_payload("product", target=None)
        stages = [
            nddev_qwen_code.prepare_anchor_publication_stage(
                anchor,
                payload,
                kind="product",
                target=None,
            )
            for _ in range(nddev_qwen_code.ANCHOR_STAGE_CANDIDATE_LIMIT)
        ]
        chosen_identity = nddev_qwen_code.identity_of(
            sorted(stages, key=lambda item: item.path.name)[0].path.lstat()
        )
        with nddev_qwen_code.anchor_lock(
            anchor, kind="product", target=None, exclusive=True, create=True
        ):
            pass
        require(
            nddev_qwen_code.identity_of(anchor.lstat()) == chosen_identity,
            "multiple valid stages did not promote deterministically",
            errors,
        )
        require(
            not any(row[0].endswith(".tmp") for row in file_tree_snapshot(root)),
            "multiple valid stage drain left residue",
            errors,
        )

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-winner-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        make_private_dir(root)
        anchor = nddev_qwen_code.product_anchor_path(root)
        payload = nddev_qwen_code.anchor_payload("product", target=None)
        losing_stage = nddev_qwen_code.prepare_anchor_publication_stage(
            anchor,
            payload,
            kind="product",
            target=None,
        )
        winning_stage = nddev_qwen_code.prepare_anchor_publication_stage(
            anchor,
            payload,
            kind="product",
            target=None,
        )
        os.link(winning_stage.path, anchor)
        nddev_qwen_code.fsync_directory(anchor.parent)
        with nddev_qwen_code.anchor_lock(
            anchor, kind="product", target=None, exclusive=True, create=True
        ):
            pass
        require(anchor.exists(), "concurrent winner final anchor missing", errors)
        require(
            anchor.lstat().st_nlink == 1, "concurrent winner final anchor nlink mismatch", errors
        )
        require(
            not losing_stage.path.exists() and not winning_stage.path.exists(),
            "concurrent winner stage drain left residue",
            errors,
        )

    def seed_stage_symlink(anchor: Path, payload: bytes) -> None:
        source = anchor.parent.parent / "stage-source"
        write_private_file(source, payload)
        os.symlink(source, nddev_qwen_code.anchor_publication_stage_path(anchor))

    def seed_stage_hardlink(anchor: Path, payload: bytes) -> None:
        first = nddev_qwen_code.anchor_publication_stage_path(anchor)
        write_private_file(first, payload)
        second = nddev_qwen_code.anchor_publication_stage_path(anchor)
        os.link(first, second)

    hostile_cases = {
        "unknown": lambda anchor, _payload: write_private_file(
            anchor.parent / "unrelated-entry",
            b"unknown",
        ),
        "malformed": lambda anchor, payload: write_private_file(
            anchor.parent / f"{nddev_qwen_code.anchor_publication_prefix_for(anchor)}bad.tmp",
            payload,
        ),
        "mismatch": lambda anchor, _payload: write_private_file(
            nddev_qwen_code.anchor_publication_stage_path(anchor),
            b'{"schema_version":1,"product_name":"wrong","kind":"product"}\n',
        ),
        "excessive": lambda anchor, payload: [
            nddev_qwen_code.prepare_anchor_publication_stage(
                anchor,
                payload,
                kind="product",
                target=None,
            )
            for _ in range(nddev_qwen_code.ANCHOR_STAGE_CANDIDATE_LIMIT + 1)
        ],
        "symlink": seed_stage_symlink,
        "hardlink": seed_stage_hardlink,
    }
    for label, seed in hostile_cases.items():
        with tempfile.TemporaryDirectory(prefix=f"nddev-qwen-anchor-{label}-") as temp_root:
            temp = Path(temp_root)
            root = temp / "control"
            make_private_dir(root)
            anchor = nddev_qwen_code.product_anchor_path(root)
            payload = nddev_qwen_code.anchor_payload("product", target=None)
            seed(anchor, payload)
            assert_anchor_failure_unchanged(
                lambda: acquire_anchor_once(anchor, "product", None),
                root,
                f"{label} publication stage",
                errors,
            )

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-cold-read-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        before_absent = file_tree_snapshot(temp)
        snapshot = nddev_qwen_code.cold_read_namespace_snapshot(root)
        require(snapshot.state == "absent", "cold read absent namespace mismatch", errors)
        require(
            file_tree_snapshot(temp) == before_absent,
            "cold read created a control namespace",
            errors,
        )
        make_private_dir(root)
        snapshot = nddev_qwen_code.cold_read_namespace_snapshot(root)
        require(snapshot.state == "empty", "cold read empty namespace mismatch", errors)
        unknown = root / "unknown.tmp"
        write_private_file(unknown, b"unknown")
        assert_anchor_failure_unchanged(
            lambda: nddev_qwen_code.cold_read_namespace_snapshot(root),
            root,
            "cold read unknown namespace entry",
            errors,
        )

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-root-") as temp_root:
        temp = Path(temp_root)
        parent = temp / "parent"
        make_private_dir(parent)
        before = nddev_qwen_code.snapshot_directory_metadata(parent, "created-root parent")
        root = parent / "control"
        root_creation = nddev_qwen_code.ensure_private_directory_component_held(root)
        nddev_qwen_code.rollback_created_directory(root_creation, "created control root")
        after = nddev_qwen_code.snapshot_directory_metadata(parent, "created-root parent")
        require(before.identity == after.identity, "created root parent metadata changed", errors)

        root.mkdir(mode=nddev_qwen_code.OWNER_DIRECTORY_MODE)
        root.chmod(nddev_qwen_code.OWNER_DIRECTORY_MODE)
        targets = root / nddev_qwen_code.TARGET_ANCHOR_DIRECTORY
        before_root = nddev_qwen_code.snapshot_directory_metadata(root, "created-targets parent")
        targets_creation = nddev_qwen_code.ensure_private_directory_component_held(targets)
        nddev_qwen_code.rollback_created_directory(targets_creation, "created targets root")
        after_root = nddev_qwen_code.snapshot_directory_metadata(root, "created-targets parent")
        require(
            before_root.identity == after_root.identity,
            "created targets parent metadata changed",
            errors,
        )

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-replace-root-") as temp_root:
        temp = Path(temp_root)
        parent = temp / "parent"
        make_private_dir(parent)
        root = parent / "control"
        signature = nddev_qwen_code.ensure_private_directory_component_held(root)
        require(signature is not None, "created control root signature missing", errors)
        if signature is not None:
            assert_created_directory_replacement_survives(signature, "created control root", errors)

    with tempfile.TemporaryDirectory(prefix="nddev-qwen-anchor-replace-targets-") as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        make_private_dir(root)
        targets = root / nddev_qwen_code.TARGET_ANCHOR_DIRECTORY
        signature = nddev_qwen_code.ensure_private_directory_component_held(targets)
        require(signature is not None, "created targets root signature missing", errors)
        if signature is not None:
            assert_created_directory_replacement_survives(signature, "created targets root", errors)

    with tempfile.TemporaryDirectory(
        prefix="nddev-qwen-anchor-replace-publish-parent-"
    ) as temp_root:
        temp = Path(temp_root)
        root = temp / "control"
        make_private_dir(root)
        target = temp / "target"
        anchor = nddev_qwen_code.target_anchor_path(root, target)
        payload = nddev_qwen_code.anchor_payload("target", target=target)
        stage = nddev_qwen_code.prepare_anchor_publication_stage(
            anchor,
            payload,
            kind="target",
            target=target,
        )
        require(
            stage.created_parent is not None,
            "target-anchor publication parent signature missing",
            errors,
        )
        stage.path.unlink()
        nddev_qwen_code.fsync_directory(stage.path.parent)
        if stage.created_parent is not None:
            assert_created_directory_replacement_survives(
                stage.created_parent,
                "target-anchor publication parent",
                errors,
            )


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
        validate_anchor_publication_contract(errors)
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
