#!/usr/bin/env python3
"""Transactional setup manager for a caller-selected Qwen Code home."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "setups"
BUILDER_ROOT = ROOT / "extensions" / "nddev-builder"
VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
PRODUCT_NAME = "nddev-qwen-code-app"
STAMP_NAME = "NDDEV-QWEN-CODE-SETUP.json"
BACKUP_NAME = "NDDEV-QWEN-CODE-BACKUP.json"
MANAGED_FILES = ("settings.json", "QWEN.md")
BUILDER_FILES = (
    "extensions/nddev-builder/qwen-extension.json",
    "extensions/nddev-builder/QWEN.md",
    "extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md",
    "extensions/nddev-builder/agents/qwen-builder-reviewer.md",
)
MANAGED_PATHS = (*MANAGED_FILES, *BUILDER_FILES, STAMP_NAME)
OWNER_FILE_MODE = 0o600
OWNER_DIRECTORY_MODE = 0o700
METADATA_MAX_BYTES = 256 * 1024
MANAGED_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
PROCESS_OUTPUT_MAX_BYTES = 256 * 1024
PROCESS_TIMEOUT_SECONDS = 120
TESTED_QWEN_CODE_VERSION = "0.21.0"
QWEN_CODE_PACKAGE = "@qwen-code/qwen-code"
QWEN_COMMAND = "qwen"
INSTALLER_URL = (
    "https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/"
    "install-qwen-standalone.sh"
)
INSTALLER_SHA256 = "6078a358a75ef3dedfa6014fa1d14984a7da15e84aa34f0077cfec59337e9638"
INSTALLER_ARGV = (
    "--method",
    "standalone",
    "--version",
    TESTED_QWEN_CODE_VERSION,
    "--no-modify-path",
)
CONTROLLED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
INSTALLER_MAX_BYTES = 1024 * 1024
SOFTWARE_TREE_MAX_BYTES = 1024 * 1024 * 1024
SOFTWARE_TREE_MAX_PATHS = 100000
SOFTWARE_DIR_RELATIVE = Path("lib") / "qwen-code"
SOFTWARE_MANIFEST_RELATIVE = Path("software") / "qwen-code.json"
SOFTWARE_REPLACE_PATHS = (
    Path("bin") / QWEN_COMMAND,
    SOFTWARE_DIR_RELATIVE,
    SOFTWARE_MANIFEST_RELATIVE,
)
TARGET_RELATIVE_QWEN_LAUNCHER = b"""#!/bin/sh
set -eu
self=$0
case "$self" in
  /*) ;;
  *) self=$(pwd -P)/$self ;;
esac
self_dir=${self%/*}
exec "$self_dir/../lib/qwen-code/bin/qwen" "$@"
"""
SOFTWARE_PARENT_PATHS = tuple(
    sorted(
        {relative.parent for relative in SOFTWARE_REPLACE_PATHS if relative.parent != Path(".")},
        key=str,
    )
)
SETUP_ID_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
STAMP_KEYS = {
    "schema_version",
    "product_name",
    "build_version",
    "setup_id",
    "canonical_target",
    "managed_paths",
}
BACKUP_KEYS = {
    "schema_version",
    "product_name",
    "build_version",
    "slot",
    "canonical_target",
    "source_setup_id",
    "managed_paths",
    "stamp_sha256",
}
SETTINGS_SETUP_KEYS = ("general", "tools", "privacy", "context")
PRESERVED_SETTINGS_KEYS = (
    "modelProviders",
    "providerProtocol",
    "security",
    "model",
    "mcpServers",
    "env",
    "telemetry",
    "proxy",
    "plansDirectory",
)
FORBIDDEN_CHILD_ENV_NAMES = {
    "ALIBABA_CLOUD_ACCESS_KEY_ID",
    "ALIBABA_CLOUD_ACCESS_KEY_SECRET",
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "BUN_AUTH_TOKEN",
    "DASHSCOPE_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "NODE_AUTH_TOKEN",
    "NPM_TOKEN",
    "OPENAI_API_KEY",
    "QWEN_API_KEY",
    "QWEN_AUTH_TOKEN",
    "SSH_ASKPASS",
    "SSH_AUTH_SOCK",
}
FORBIDDEN_CHILD_ENV_PREFIXES = (
    "BUN_CONFIG_",
    "npm_config_",
)
QWEN_SCOPE_FLAGS_WITH_VALUE = {
    "--add-dir",
    "--allowed-mcp-server-names",
    "--allowed-tools",
    "--append-system-prompt",
    "--approval-mode",
    "--auth-type",
    "--core-tools",
    "--exclude-tools",
    "--extensions",
    "--fallback-model",
    "--include-directories",
    "--json-fd",
    "--json-file",
    "--json-schema",
    "--max-session-turns",
    "--max-subagent-depth",
    "--max-tool-calls",
    "--mcp-config",
    "--model",
    "--openai-api-key",
    "--openai-base-url",
    "--openai-logging-dir",
    "--proxy",
    "--resume",
    "--sandbox-image",
    "--sandbox-session-id",
    "--session-id",
    "--system-prompt",
    "--telemetry-otlp-endpoint",
    "--telemetry-otlp-protocol",
    "--telemetry-outfile",
    "--telemetry-target",
    "--worktree",
    "-e",
    "-m",
    "-r",
}
QWEN_SCOPE_FLAGS_WITHOUT_VALUE = {
    "--chat-recording",
    "--continue",
    "--debug",
    "--fork-session",
    "--list-extensions",
    "--openai-logging",
    "--safe-mode",
    "--sandbox",
    "--telemetry",
    "--telemetry-log-prompts",
    "--yolo",
    "-c",
    "-d",
    "-l",
    "-s",
    "-y",
}


class QwenCodeSetupError(Exception):
    """A safe user-facing lifecycle failure."""


class ConcurrentTargetChange(QwenCodeSetupError):
    """A fail-closed target race."""


@dataclass(frozen=True)
class FileSnapshot:
    digest: str
    mode: int
    inode: tuple[int, int]
    owner: int | None


def fail(message: str) -> NoReturn:
    raise QwenCodeSetupError(message)


def fail_concurrent(message: str) -> NoReturn:
    raise ConcurrentTargetChange(message)


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def identity_of(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def owner_of(info: os.stat_result) -> int | None:
    return info.st_uid if hasattr(info, "st_uid") else None


def is_owner_only_file(info: os.stat_result) -> bool:
    if stat.S_IMODE(info.st_mode) != OWNER_FILE_MODE:
        return False
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        return False
    return True


def is_owner_private_directory(info: os.stat_result) -> bool:
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != OWNER_DIRECTORY_MODE:
        return False
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        return False
    return True


def require_directory(path: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{label} must be a real directory")
    return info


def require_regular_file(path: Path, label: str, *, owner_only: bool = False) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if owner_only and not is_owner_only_file(info):
        fail(f"{label} must be owned by the current user with mode 0600")
    if info.st_size > MANAGED_PAYLOAD_MAX_BYTES:
        fail(f"{label} exceeds the {MANAGED_PAYLOAD_MAX_BYTES}-byte size limit")
    return info


def read_regular_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    max_bytes: int = MANAGED_PAYLOAD_MAX_BYTES,
) -> tuple[bytes, os.stat_result]:
    before = require_regular_file(path, label, owner_only=owner_only)
    if before.st_size > max_bytes:
        fail(f"{label} exceeds the {max_bytes}-byte size limit")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if identity_of(opened) != identity_of(before):
            fail_concurrent(f"{label} changed while it was being opened")
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            fail(f"{label} changed to an unsafe file")
        if owner_only and not is_owner_only_file(opened):
            fail(f"{label} must be owned by the current user with mode 0600")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                fail(f"{label} exceeds the {max_bytes}-byte size limit")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = require_regular_file(path, label, owner_only=owner_only)
    if identity_of(after) != identity_of(before) or identity_of(final) != identity_of(before):
        fail_concurrent(f"{label} changed while it was being read")
    return b"".join(chunks), final


def parse_json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        fail(f"cannot read valid JSON from {label}: {exc}")
    if not isinstance(value, dict):
        fail(f"{label} must contain a JSON object")
    return value


def load_json_object(path: Path, label: str, *, owner_only: bool = False) -> dict[str, Any]:
    content, _ = read_regular_file(
        path,
        label,
        owner_only=owner_only,
        max_bytes=METADATA_MAX_BYTES,
    )
    return parse_json_object(content, label)


def require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        fail(
            f"{label} has invalid keys "
            f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
        )


def validate_setup_id(setup_id: str) -> None:
    if not SETUP_ID_PATTERN.fullmatch(setup_id):
        fail(f"invalid setup id: {setup_id!r}")


def ensure_lf_text(content: bytes, label: str) -> None:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{label} must be valid UTF-8: {exc}")
    if not content or not content.endswith(b"\n") or b"\r" in content:
        fail(f"{label} must be non-empty LF-terminated text")


def validate_setup_settings(setup_id: str, settings: dict[str, Any]) -> None:
    expected = {
        "safe": ("default", True),
        "balanced": ("auto-edit", True),
        "full-auto": ("yolo", False),
    }
    if setup_id not in expected:
        fail(f"unsupported setup id: {setup_id}")
    tools = settings.get("tools")
    privacy = settings.get("privacy")
    context = settings.get("context")
    if not isinstance(tools, dict):
        fail(f"setup {setup_id}/settings.json tools must be an object")
    if not isinstance(privacy, dict) or privacy.get("usageStatisticsEnabled") is not False:
        fail(f"setup {setup_id}/settings.json must disable usage statistics")
    if not isinstance(context, dict) or context.get("fileName") != ["QWEN.md"]:
        fail(f"setup {setup_id}/settings.json must select QWEN.md context")
    approval, sandbox = expected[setup_id]
    if tools.get("approvalMode") != approval or tools.get("sandbox") is not sandbox:
        fail(f"setup {setup_id}/settings.json approval or sandbox policy mismatch")


def render_builder() -> dict[str, bytes]:
    files = {
        "extensions/nddev-builder/qwen-extension.json": BUILDER_ROOT / "qwen-extension.json",
        "extensions/nddev-builder/QWEN.md": BUILDER_ROOT / "QWEN.md",
        "extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md": (
            BUILDER_ROOT / "skills" / "qwen-builder-orientation" / "SKILL.md"
        ),
        "extensions/nddev-builder/agents/qwen-builder-reviewer.md": (
            BUILDER_ROOT / "agents" / "qwen-builder-reviewer.md"
        ),
    }
    rendered: dict[str, bytes] = {}
    for relative, source in files.items():
        content, _ = read_regular_file(source, f"builder source {relative}")
        if relative.endswith(".json"):
            parse_json_object(content, f"builder source {relative}")
        else:
            ensure_lf_text(content, f"builder source {relative}")
        rendered[relative] = content
    extension = parse_json_object(
        rendered["extensions/nddev-builder/qwen-extension.json"], "builder extension"
    )
    require_exact_keys(
        extension,
        {"name", "displayName", "description", "version", "contextFileName", "skills", "agents"},
        "builder extension",
    )
    if extension["name"] != "nddev-builder":
        fail("builder extension identity mismatch")
    return rendered


def render_setup(setup_id: str) -> tuple[dict[str, Any], dict[str, bytes]]:
    validate_setup_id(setup_id)
    setup_root = CATALOG_ROOT / setup_id
    if not setup_root.is_dir() or setup_root.is_symlink():
        fail(f"unknown setup: {setup_id}")

    metadata = load_json_object(setup_root / "setup.json", f"setup {setup_id} metadata")
    require_exact_keys(
        metadata,
        {
            "schema_version",
            "id",
            "description",
            "managed_files",
            "builder_extension",
            "builder_default_on",
        },
        f"setup {setup_id} metadata",
    )
    if metadata["schema_version"] != 1 or metadata["id"] != setup_id:
        fail(f"setup {setup_id} metadata identity or schema mismatch")
    if metadata["managed_files"] != list(MANAGED_FILES):
        fail(f"setup {setup_id} managed file declaration is invalid")
    if metadata["builder_extension"] != "extensions/nddev-builder":
        fail(f"setup {setup_id} builder extension declaration is invalid")
    if metadata["builder_default_on"] is not True:
        fail(f"setup {setup_id} must enable builder by default")
    if not isinstance(metadata["description"], str) or not metadata["description"].strip():
        fail(f"setup {setup_id} description must be non-empty")

    settings_content, _ = read_regular_file(
        setup_root / "settings.json",
        f"setup {setup_id}/settings.json",
        max_bytes=METADATA_MAX_BYTES,
    )
    settings = parse_json_object(settings_content, f"setup {setup_id}/settings.json")
    validate_setup_settings(setup_id, settings)
    qwen_content, _ = read_regular_file(setup_root / "QWEN.md", f"setup {setup_id}/QWEN.md")
    ensure_lf_text(qwen_content, f"setup {setup_id}/QWEN.md")
    return metadata, {
        "settings.json": canonical_json(settings),
        "QWEN.md": qwen_content,
        **render_builder(),
    }


def list_setups() -> list[dict[str, Any]]:
    if not CATALOG_ROOT.is_dir() or CATALOG_ROOT.is_symlink():
        fail("setup catalog is missing or unsafe")
    entries: list[dict[str, Any]] = []
    for candidate in sorted(CATALOG_ROOT.iterdir(), key=lambda path: path.name):
        if not candidate.is_dir() or candidate.is_symlink():
            fail(f"catalog entry must be a real directory: {candidate.name}")
        metadata, _ = render_setup(candidate.name)
        entries.append(
            {
                "id": metadata["id"],
                "description": metadata["description"],
                "managed_files": metadata["managed_files"],
                "builder_default_on": metadata["builder_default_on"],
            }
        )
    if not entries:
        fail("setup catalog is empty")
    return entries


def resolve_target(raw_target: str) -> Path:
    expanded = Path(raw_target).expanduser()
    if not expanded.is_absolute():
        fail("--target must be an absolute path")
    try:
        raw_info = expanded.lstat()
    except FileNotFoundError:
        raw_info = None
    if raw_info is not None and stat.S_ISLNK(raw_info.st_mode):
        fail("--target must not be a symlink")
    target = expanded.resolve(strict=False)
    if target == Path(target.anchor):
        fail("filesystem root cannot be a target")
    parent = target.parent
    try:
        parent_info = parent.lstat()
    except FileNotFoundError:
        fail("--target parent must already exist")
    if stat.S_ISLNK(parent_info.st_mode) or not stat.S_ISDIR(parent_info.st_mode):
        fail("canonical --target parent must be a real directory")
    if target.exists():
        target_info = target.lstat()
        if stat.S_ISLNK(target_info.st_mode) or not stat.S_ISDIR(target_info.st_mode):
            fail("--target must be a real directory when it exists")
    return target


def ensure_private_directory(path: Path, *, create: bool) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not create:
            return False
        path.mkdir(mode=OWNER_DIRECTORY_MODE)
        path.chmod(OWNER_DIRECTORY_MODE)
        return True
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"{path} must be a real directory")
    return True


def require_private_target_directory_for_software(target: Path, *, allow_missing: bool) -> bool:
    try:
        info = target.lstat()
    except FileNotFoundError:
        if allow_missing:
            return False
        fail("software target is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail("software target must be a real directory")
    if not is_owner_private_directory(info):
        fail("software target must be private to the current user with mode 0700")
    return True


def mkdirs_for_file(path: Path) -> None:
    parents: list[Path] = []
    cursor = path.parent
    while cursor and not cursor.exists():
        parents.append(cursor)
        cursor = cursor.parent
    for directory in reversed(parents):
        directory.mkdir(mode=OWNER_DIRECTORY_MODE)
        directory.chmod(OWNER_DIRECTORY_MODE)
    for directory in path.parents:
        if directory == path.anchor:
            break
        if not directory.exists():
            continue
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            fail(f"managed path parent is unsafe: {directory}")
        if directory.name == ".nddev-qwen-code.lock":
            continue
        if directory.parent.exists() and directory.name.startswith("."):
            continue
        try:
            directory.chmod(OWNER_DIRECTORY_MODE)
        except PermissionError:
            pass
        if directory == path.parent:
            break


def target_path(target: Path, name: str) -> Path:
    path = target / name
    try:
        path.relative_to(target)
    except ValueError:
        fail(f"managed path escapes target: {name}")
    return path


def path_exists_no_follow(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def snapshot_file(path: Path, *, owner_only: bool = False) -> FileSnapshot | None:
    if not path_exists_no_follow(path):
        return None
    content, info = read_regular_file(path, f"managed path {path}", owner_only=owner_only)
    return FileSnapshot(
        digest=sha256_bytes(content),
        mode=stat.S_IMODE(info.st_mode),
        inode=identity_of(info),
        owner=owner_of(info),
    )


def snapshot_managed(target: Path, *, owner_only: bool = True) -> dict[str, FileSnapshot | None]:
    return {
        name: snapshot_file(target_path(target, name), owner_only=owner_only)
        for name in MANAGED_PATHS
    }


def validate_digest_map(value: Any, label: str) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) != set((*MANAGED_FILES, *BUILDER_FILES)):
        fail(f"{label} must declare exactly managed payload paths")
    result: dict[str, str | None] = {}
    for name in (*MANAGED_FILES, *BUILDER_FILES):
        digest = value[name]
        if digest is not None and (
            not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None
        ):
            fail(f"{label}.{name} must be null or a lowercase SHA-256 digest")
        result[name] = digest
    return result


def stamp_bytes(target: Path, setup_id: str, rendered: dict[str, bytes]) -> bytes:
    return canonical_json(
        {
            "schema_version": 1,
            "product_name": PRODUCT_NAME,
            "build_version": VERSION,
            "setup_id": setup_id,
            "canonical_target": str(target),
            "managed_paths": {
                name: sha256_bytes(rendered[name]) for name in (*MANAGED_FILES, *BUILDER_FILES)
            },
        }
    )


def load_stamp(target: Path) -> dict[str, Any] | None:
    stamp = target / STAMP_NAME
    if not path_exists_no_follow(stamp):
        return None
    content, _ = read_regular_file(
        stamp,
        f"managed stamp {stamp}",
        owner_only=False,
        max_bytes=METADATA_MAX_BYTES,
    )
    value = parse_json_object(content, f"managed stamp {stamp}")
    require_exact_keys(value, STAMP_KEYS, "managed stamp")
    if value["schema_version"] != 1 or value["product_name"] != PRODUCT_NAME:
        fail("managed stamp identity or schema is invalid")
    if (
        not isinstance(value["build_version"], str)
        or SEMVER_PATTERN.fullmatch(value["build_version"]) is None
    ):
        fail("managed stamp build version is invalid")
    if value["canonical_target"] != str(target):
        fail("managed stamp is bound to a different canonical target")
    if not isinstance(value["setup_id"], str):
        fail("managed stamp setup_id must be a string")
    validate_setup_id(value["setup_id"])
    validate_digest_map(value["managed_paths"], "managed stamp managed_paths")
    return value


def settings_overlay(current: bytes, setup_id: str) -> dict[str, Any]:
    try:
        current_settings = json.loads(current.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(current_settings, dict):
        return {}
    _, base = render_setup(setup_id)
    base_settings = parse_json_object(base["settings.json"], f"setup {setup_id}/settings.json")
    overlay: dict[str, Any] = {}
    for key, value in current_settings.items():
        if key in SETTINGS_SETUP_KEYS and value != base_settings.get(key):
            continue
        if key not in SETTINGS_SETUP_KEYS:
            overlay[key] = value
    for key in PRESERVED_SETTINGS_KEYS:
        if key in current_settings:
            overlay[key] = current_settings[key]
    return overlay


def merge_settings(base: bytes, overlay: dict[str, Any]) -> bytes:
    settings = parse_json_object(base, "rendered settings")
    for key, value in overlay.items():
        if key not in SETTINGS_SETUP_KEYS:
            settings[key] = value
    return canonical_json(settings)


def settings_managed_intact(current: bytes, setup_id: str) -> bool:
    try:
        current_settings = json.loads(current.decode("utf-8"))
        _, rendered = render_setup(setup_id)
        base = parse_json_object(rendered["settings.json"], f"setup {setup_id}/settings.json")
    except (UnicodeDecodeError, json.JSONDecodeError, QwenCodeSetupError):
        return False
    if not isinstance(current_settings, dict):
        return False
    for key in SETTINGS_SETUP_KEYS:
        if current_settings.get(key) != base.get(key):
            return False
    return True


def inspect_target(target: Path) -> dict[str, Any]:
    if not ensure_private_directory(target, create=False):
        return {
            "state": "missing",
            "setup_id": None,
            "build_version": None,
            "drift": [],
            "unmanaged_managed_paths": [],
            "builder_extension": "missing",
        }
    stamp = load_stamp(target)
    existing: list[str] = []
    for name in (*MANAGED_FILES, *BUILDER_FILES):
        path = target_path(target, name)
        if path_exists_no_follow(path):
            require_regular_file(path, f"managed path {path}")
            existing.append(name)
    if stamp is None:
        return {
            "state": "unmanaged",
            "setup_id": None,
            "build_version": None,
            "drift": [],
            "unmanaged_managed_paths": existing,
            "builder_extension": "present"
            if (target / "extensions" / "nddev-builder").exists()
            else "missing",
        }
    expected = validate_digest_map(stamp["managed_paths"], "managed stamp managed_paths")
    drift: list[str] = []
    for name in (*MANAGED_FILES, *BUILDER_FILES):
        path = target_path(target, name)
        snapshot = snapshot_file(path, owner_only=False)
        if expected[name] is None or snapshot is None:
            drift.append(name)
            continue
        owner_matches = not hasattr(os, "geteuid") or snapshot.owner == os.geteuid()
        if snapshot.mode != OWNER_FILE_MODE or not owner_matches:
            drift.append(name)
            continue
        if snapshot.digest == expected[name]:
            continue
        if name == "settings.json" and settings_managed_intact(
            read_regular_file(path, f"managed path {path}")[0],
            stamp["setup_id"],
        ):
            continue
        drift.append(name)
    stamp_snapshot = snapshot_file(target / STAMP_NAME, owner_only=False)
    if stamp_snapshot is None:
        drift.append(STAMP_NAME)
    elif stamp_snapshot.mode != OWNER_FILE_MODE:
        drift.append(STAMP_NAME)
    return {
        "state": "managed",
        "setup_id": stamp["setup_id"],
        "build_version": stamp["build_version"],
        "drift": drift,
        "unmanaged_managed_paths": [],
        "builder_extension": "present"
        if not any(name in drift for name in BUILDER_FILES)
        else "drift",
    }


def require_clean_managed(target: Path) -> dict[str, Any]:
    status = inspect_target(target)
    if status["state"] != "managed":
        fail("target is not managed by nddev-qwen-code-app")
    if status["drift"]:
        fail(f"managed target has drift: {', '.join(status['drift'])}")
    return status


def backup_pool(target: Path) -> Path:
    return target.parent / f".{target.name}.nddev-qwen-code-backups"


def lock_path(target: Path) -> Path:
    return target.parent / f".{target.name}.nddev-qwen-code.lock"


@contextlib.contextmanager
def target_lock(target: Path) -> Iterator[None]:
    lock = lock_path(target)
    require_directory(target.parent, "canonical --target parent")
    try:
        lock.mkdir(mode=OWNER_DIRECTORY_MODE)
        lock.chmod(OWNER_DIRECTORY_MODE)
        (lock / "owner.json").write_bytes(
            canonical_json({"schema_version": 1, "pid": os.getpid(), "target": str(target)})
        )
        (lock / "owner.json").chmod(OWNER_FILE_MODE)
    except FileExistsError:
        fail(f"target is locked: {lock}")
    try:
        yield
    finally:
        try:
            owner = lock / "owner.json"
            if owner.exists() and not owner.is_symlink():
                owner.unlink()
            lock.rmdir()
        except OSError as exc:
            raise QwenCodeSetupError(f"target lock cleanup failed: {lock}") from exc


def atomic_write(path: Path, content: bytes) -> None:
    mkdirs_for_file(path)
    if path.exists() or path.is_symlink():
        require_regular_file(path, f"managed path {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(OWNER_FILE_MODE)
        os.replace(temp_path, path)
        path.chmod(OWNER_FILE_MODE)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def prune_empty_managed_dirs(target: Path) -> None:
    for relative in (
        "extensions/nddev-builder/skills/qwen-builder-orientation",
        "extensions/nddev-builder/skills",
        "extensions/nddev-builder/agents",
        "extensions/nddev-builder",
        "extensions",
    ):
        path = target / relative
        try:
            path.rmdir()
        except OSError:
            pass


def assert_snapshot(target: Path, expected: dict[str, FileSnapshot | None]) -> None:
    actual = snapshot_managed(target, owner_only=False)
    for name, value in expected.items():
        if actual[name] != value:
            fail_concurrent(f"managed path changed concurrently: {target_path(target, name)}")


def replace_managed_state(
    target: Path,
    desired: dict[str, bytes | None],
    expected: dict[str, FileSnapshot | None],
) -> None:
    assert_snapshot(target, expected)
    for name in MANAGED_PATHS:
        path = target_path(target, name)
        content = desired.get(name)
        if content is None:
            if path_exists_no_follow(path):
                if snapshot_file(path, owner_only=False) != expected[name]:
                    fail_concurrent(f"managed path changed concurrently: {path}")
                path.unlink()
            continue
        atomic_write(path, content)
    prune_empty_managed_dirs(target)
    for name in MANAGED_PATHS:
        content = desired.get(name)
        path = target_path(target, name)
        if content is None:
            if path_exists_no_follow(path):
                fail_concurrent(f"managed path appeared after removal: {path}")
        else:
            snapshot = snapshot_file(path, owner_only=True)
            if snapshot is None or snapshot.digest != sha256_bytes(content):
                fail_concurrent(f"managed path changed after replacement: {path}")


def remove_created_target_if_empty(target: Path, existed_before: bool) -> None:
    if existed_before:
        return
    try:
        if target.exists() and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    except OSError:
        pass


def create_backup(target: Path, exclude: int | None = None) -> tuple[int, dict[str, bytes | None]]:
    pool = backup_pool(target)
    ensure_private_directory(pool, create=True)
    pool.chmod(OWNER_DIRECTORY_MODE)
    status = inspect_target(target)
    desired: dict[str, bytes | None] = {name: None for name in MANAGED_PATHS}
    digests: dict[str, str | None] = {}
    for name in (*MANAGED_FILES, *BUILDER_FILES):
        path = target_path(target, name)
        if path_exists_no_follow(path):
            content, _ = read_regular_file(path, f"managed path {path}", owner_only=True)
            desired[name] = content
            digests[name] = sha256_bytes(content)
        else:
            digests[name] = None
    stamp_digest: str | None = None
    stamp = target / STAMP_NAME
    if path_exists_no_follow(stamp):
        content, _ = read_regular_file(
            stamp,
            f"managed stamp {stamp}",
            owner_only=True,
            max_bytes=METADATA_MAX_BYTES,
        )
        desired[STAMP_NAME] = content
        stamp_digest = sha256_bytes(content)
    slot = choose_backup_slot(pool, target, exclude=exclude)
    staging = pool / f".{slot}.new-{os.getpid()}-{time.time_ns()}"
    if staging.exists():
        fail("backup staging path collision")
    staging.mkdir(mode=OWNER_DIRECTORY_MODE)
    payload = staging / "payload"
    payload.mkdir(mode=OWNER_DIRECTORY_MODE)
    envelope = {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "build_version": VERSION,
        "slot": slot,
        "canonical_target": str(target),
        "source_setup_id": status["setup_id"] if status["state"] == "managed" else None,
        "managed_paths": digests,
        "stamp_sha256": stamp_digest,
    }
    try:
        for name, content in desired.items():
            if content is not None:
                atomic_write(payload / name, content)
        atomic_write(staging / BACKUP_NAME, canonical_json(envelope))
        destination = pool / str(slot)
        replaced = pool / f".{slot}.replaced"
        if replaced.exists():
            shutil.rmtree(replaced)
        if destination.exists():
            destination.rename(replaced)
        staging.rename(destination)
        if replaced.exists():
            shutil.rmtree(replaced)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return slot, desired


def choose_backup_slot(pool: Path, target: Path, exclude: int | None = None) -> int:
    for slot in range(10):
        if not (pool / str(slot)).exists():
            return slot
    candidates: list[tuple[float, int]] = []
    for slot in range(10):
        if slot == exclude:
            continue
        load_backup(target, slot)
        candidates.append(((pool / str(slot)).stat().st_mtime, slot))
    if not candidates:
        fail("no backup slot is available without destroying the restore source")
    return min(candidates)[1]


def load_backup(target: Path, slot: int) -> tuple[dict[str, Any], dict[str, bytes | None]]:
    if not 0 <= slot <= 9:
        fail("--backup must be an integer from 0 to 9")
    slot_root = backup_pool(target) / str(slot)
    info = require_directory(slot_root, f"backup slot {slot}")
    if not is_owner_private_directory(info):
        fail(f"backup slot {slot} must be owned by the current user with mode 0700")
    envelope = load_json_object(
        slot_root / BACKUP_NAME,
        f"backup slot {slot}",
        owner_only=True,
    )
    require_exact_keys(envelope, BACKUP_KEYS, f"backup slot {slot}")
    if (
        envelope["schema_version"] != 1
        or envelope["product_name"] != PRODUCT_NAME
        or envelope["slot"] != slot
    ):
        fail(f"backup slot {slot} identity or schema is invalid")
    if envelope["canonical_target"] != str(target):
        fail(f"backup slot {slot} is bound to a different canonical target")
    digests = validate_digest_map(envelope["managed_paths"], f"backup slot {slot}")
    stamp_digest = envelope["stamp_sha256"]
    if stamp_digest is not None and (
        not isinstance(stamp_digest, str) or SHA256_PATTERN.fullmatch(stamp_digest) is None
    ):
        fail(f"backup slot {slot} stamp_sha256 must be null or a SHA-256 digest")
    payload_root = slot_root / "payload"
    require_directory(payload_root, f"backup slot {slot} payload")
    desired: dict[str, bytes | None] = {name: None for name in MANAGED_PATHS}
    for name, digest in digests.items():
        if digest is None:
            continue
        content, _ = read_regular_file(
            payload_root / name,
            f"backup slot {slot} payload {name}",
            owner_only=True,
        )
        if sha256_bytes(content) != digest:
            fail(f"backup slot {slot} payload digest mismatch for {name}")
        desired[name] = content
    if stamp_digest is not None:
        content, _ = read_regular_file(
            payload_root / STAMP_NAME,
            f"backup slot {slot} managed stamp",
            owner_only=True,
            max_bytes=METADATA_MAX_BYTES,
        )
        if sha256_bytes(content) != stamp_digest:
            fail(f"backup slot {slot} managed stamp digest mismatch")
        desired[STAMP_NAME] = content
    return envelope, desired


def desired_for_setup(
    target: Path, setup_id: str, *, preserve_from_current: bool
) -> dict[str, bytes]:
    _, rendered = render_setup(setup_id)
    if preserve_from_current and path_exists_no_follow(target / "settings.json"):
        current, _ = read_regular_file(target / "settings.json", "managed settings.json")
        rendered["settings.json"] = merge_settings(
            rendered["settings.json"],
            settings_overlay(
                current, load_stamp(target)["setup_id"] if load_stamp(target) else setup_id
            ),
        )
    return {**rendered, STAMP_NAME: stamp_bytes(target, setup_id, rendered)}


def plan_setup(target: Path, setup_id: str) -> dict[str, Any]:
    validate_setup_id(setup_id)
    render_setup(setup_id)
    status = inspect_target(target)
    if status["state"] == "unmanaged" and status["unmanaged_managed_paths"]:
        fail(
            "unmanaged target already contains managed paths: "
            + ", ".join(status["unmanaged_managed_paths"])
        )
    if status["state"] == "managed" and status["drift"]:
        fail(f"managed target has drift: {', '.join(status['drift'])}")
    if status["state"] in {"missing", "unmanaged"}:
        operation = "install"
    elif status["setup_id"] == setup_id:
        operation = "update"
    else:
        operation = "switch"
    desired = desired_for_setup(
        target,
        setup_id,
        preserve_from_current=status["state"] == "managed",
    )
    changes: list[str] = []
    for name in MANAGED_PATHS:
        path = target_path(target, name)
        snapshot = snapshot_file(path, owner_only=False)
        current = snapshot.digest if snapshot is not None else None
        wanted = sha256_bytes(desired[name]) if name in desired else None
        if current != wanted:
            changes.append(name)
    return {
        "schema_version": 1,
        "command": "plan",
        "target": str(target),
        "setup_id": setup_id,
        "operation": operation,
        "changes": changes,
        "backup_required": status["state"] == "managed",
        "mutates": False,
    }


def rollback_to(target: Path, rollback_desired: dict[str, bytes | None]) -> None:
    current = snapshot_managed(target, owner_only=False)
    replace_managed_state(target, rollback_desired, current)


def mutate_setup(target: Path, setup_id: str, command: str) -> dict[str, Any]:
    plan = plan_setup(target, setup_id)
    if command == "install" and plan["operation"] == "switch":
        fail("install cannot change setup identity; use switch")
    if command == "switch" and plan["operation"] != "switch":
        fail("switch requires a managed target with a different setup")
    if not plan["changes"]:
        return {
            "schema_version": 1,
            "command": command,
            "target": str(target),
            "setup_id": setup_id,
            "changed": [],
            "backup_slot": None,
        }
    existed_before = target.exists()
    with target_lock(target):
        prior_status = inspect_target(target)
        plan = plan_setup(target, setup_id)
        if command == "install" and plan["operation"] == "switch":
            fail("install cannot change setup identity; use switch")
        if command == "switch" and plan["operation"] != "switch":
            fail("switch requires a managed target with a different setup")
        backup_slot: int | None = None
        rollback_desired: dict[str, bytes | None] | None = None
        before = snapshot_managed(target, owner_only=True)
        if plan["backup_required"]:
            backup_slot, rollback_desired = create_backup(target)
            before = snapshot_managed(target, owner_only=True)
        try:
            ensure_private_directory(target, create=True)
            desired = desired_for_setup(
                target,
                setup_id,
                preserve_from_current=prior_status["state"] == "managed",
            )
            replace_managed_state(target, desired, before)
            final = require_clean_managed(target)
            if final["setup_id"] != setup_id:
                fail("postcondition failed: setup identity mismatch")
        except BaseException:
            if rollback_desired is not None:
                rollback_to(target, rollback_desired)
            else:
                empty = {name: None for name in MANAGED_PATHS}
                rollback_to(target, empty)
                remove_created_target_if_empty(target, existed_before)
            raise
    return {
        "schema_version": 1,
        "command": command,
        "target": str(target),
        "setup_id": setup_id,
        "changed": plan["changes"],
        "backup_slot": backup_slot,
    }


def restore_slot(target: Path, slot: int) -> dict[str, Any]:
    status = inspect_target(target)
    if status["state"] == "unmanaged" and status["unmanaged_managed_paths"]:
        fail("cannot restore over unmanaged managed paths")
    if status["state"] == "managed" and status["drift"]:
        fail(f"managed target has drift: {', '.join(status['drift'])}")
    with target_lock(target):
        current = inspect_target(target)
        if current["state"] == "managed" and current["drift"]:
            fail(f"managed target has drift: {', '.join(current['drift'])}")
        envelope, restore_desired = load_backup(target, slot)
        if envelope["source_setup_id"] is None:
            fail("selected backup does not contain a managed Qwen Code setup")
        before = snapshot_managed(target, owner_only=True)
        rollback_slot, rollback_desired = create_backup(target, exclude=slot)
        try:
            ensure_private_directory(target, create=True)
            replace_managed_state(target, restore_desired, before)
            final = require_clean_managed(target)
            if final["setup_id"] != envelope["source_setup_id"]:
                fail("postcondition failed: restored setup identity mismatch")
        except BaseException:
            rollback_to(target, rollback_desired)
            raise
    return {
        "schema_version": 1,
        "command": "restore",
        "target": str(target),
        "setup_id": envelope["source_setup_id"],
        "restored_backup_slot": slot,
        "rollback_backup_slot": rollback_slot,
    }


def remove_setup(target: Path) -> dict[str, Any]:
    status = require_clean_managed(target)
    with target_lock(target):
        status = require_clean_managed(target)
        before = snapshot_managed(target, owner_only=True)
        backup_slot, rollback_desired = create_backup(target)
        try:
            desired = {name: None for name in MANAGED_PATHS}
            replace_managed_state(target, desired, before)
        except BaseException:
            rollback_to(target, rollback_desired)
            raise
    return {
        "schema_version": 1,
        "command": "remove",
        "target": str(target),
        "removed_setup_id": status["setup_id"],
        "backup_slot": backup_slot,
    }


def qwen_executable(target: Path) -> Path:
    return target / "bin" / QWEN_COMMAND


def software_manifest_path(target: Path) -> Path:
    return target / SOFTWARE_MANIFEST_RELATIVE


def require_safe_executable(path: Path, root: Path, label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        fail(f"{label} must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o700:
        fail(f"{label} must be private to the current user with mode 0700")
    try:
        path.resolve(strict=True).relative_to(root.resolve())
    except ValueError:
        fail(f"{label} must stay inside the target")
    return info


def chmod_private_tree(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts)):
        if path.is_symlink():
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            path.chmod(OWNER_DIRECTORY_MODE)
        elif stat.S_ISREG(info.st_mode):
            if stat.S_IMODE(info.st_mode) & stat.S_IXUSR:
                path.chmod(0o700)
            else:
                path.chmod(OWNER_FILE_MODE)


def safe_child_base_environment(*, include_path: bool) -> dict[str, str]:
    env: dict[str, str] = {}
    if include_path:
        env["PATH"] = CONTROLLED_PATH
    for name in ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "COLORTERM", "NO_COLOR"):
        value = os.environ.get(name)
        if value:
            env[name] = value
    return env


def install_stage_environment(stage_root: Path, live_stage: Path) -> dict[str, str]:
    home = stage_root / "home"
    tmp = stage_root / "tmp"
    xdg_config = stage_root / "xdg-config"
    xdg_cache = stage_root / "xdg-cache"
    xdg_state = stage_root / "xdg-state"
    qwen_home = stage_root / "qwen-home"
    qwen_runtime = stage_root / "qwen-runtime"
    for directory in (home, tmp, xdg_config, xdg_cache, xdg_state, qwen_home, qwen_runtime):
        directory.mkdir(mode=OWNER_DIRECTORY_MODE, parents=True, exist_ok=True)
        directory.chmod(OWNER_DIRECTORY_MODE)
    env = safe_child_base_environment(include_path=True)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "TMPDIR": str(tmp),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_STATE_HOME": str(xdg_state),
            "QWEN_HOME": str(qwen_home),
            "QWEN_RUNTIME_DIR": str(qwen_runtime),
            "QWEN_INSTALL_ROOT": str(live_stage),
        }
    )
    return env


def launch_environment(target: Path) -> dict[str, str]:
    runtime = target / "runtime"
    tmp = runtime / "tmp"
    xdg_config = runtime / "xdg-config"
    xdg_cache = runtime / "xdg-cache"
    xdg_state = runtime / "xdg-state"
    for directory in (runtime, tmp, xdg_config, xdg_cache, xdg_state):
        directory.mkdir(mode=OWNER_DIRECTORY_MODE, parents=True, exist_ok=True)
        directory.chmod(OWNER_DIRECTORY_MODE)
    env = safe_child_base_environment(include_path=False)
    env.update(
        {
            "HOME": str(target),
            "USERPROFILE": str(target),
            "QWEN_HOME": str(target),
            "QWEN_RUNTIME_DIR": str(runtime),
            "TMPDIR": str(tmp),
            "XDG_CONFIG_HOME": str(xdg_config),
            "XDG_CACHE_HOME": str(xdg_cache),
            "XDG_STATE_HOME": str(xdg_state),
        }
    )
    return env


def download_bytes(url: str, *, max_bytes: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": f"{PRODUCT_NAME}/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=PROCESS_TIMEOUT_SECONDS) as response:
            length_header = response.headers.get("Content-Length")
            expected_length: int | None = None
            if length_header:
                try:
                    expected_length = int(length_header)
                except ValueError:
                    fail(f"download from {url} returned an invalid Content-Length")
                if expected_length < 0:
                    fail(f"download from {url} returned an invalid Content-Length")
                if expected_length > max_bytes:
                    fail(f"download from {url} declared {expected_length} bytes over the limit")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    fail(f"download from {url} exceeded {max_bytes} bytes")
                chunks.append(chunk)
            if expected_length is not None and total != expected_length:
                fail(
                    f"download from {url} ended at {total} bytes, "
                    f"expected {expected_length} from Content-Length"
                )
    except (urllib.error.URLError, TimeoutError) as exc:
        fail(f"failed to download official Qwen Code installer: {exc}")
    return b"".join(chunks)


def download_official_installer(stage_root: Path) -> Path:
    content = download_bytes(INSTALLER_URL, max_bytes=INSTALLER_MAX_BYTES)
    digest = sha256_bytes(content)
    if digest != INSTALLER_SHA256:
        fail("official Qwen Code installer SHA-256 mismatch")
    installer = stage_root / "install-qwen-standalone.sh"
    installer.write_bytes(content)
    installer.chmod(0o700)
    return installer


def observed_qwen_version(executable: Path, target: Path) -> str:
    require_safe_executable(executable, target, "staged Qwen Code executable")
    completed = bounded_process(
        [str(executable), "--version"],
        cwd=target,
        env=launch_environment(target),
        timeout=20,
    )
    if completed.returncode != 0:
        fail(f"Qwen Code version smoke failed with exit {completed.returncode}")
    text = "\n".join((completed.stdout, completed.stderr)).strip()
    match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])", text)
    if match is None or SEMVER_PATTERN.fullmatch(match.group(1)) is None:
        fail(f"Qwen Code returned an invalid version string: {text!r}")
    return match.group(1)


def write_target_relative_qwen_launcher(root: Path) -> None:
    launcher = root / "bin" / QWEN_COMMAND
    package_entrypoint = root / SOFTWARE_DIR_RELATIVE / "bin" / QWEN_COMMAND
    require_safe_executable(launcher, root, "official Qwen Code launcher")
    require_safe_executable(package_entrypoint, root, "Qwen Code package entrypoint")
    fd, temporary = tempfile.mkstemp(
        prefix=f".{QWEN_COMMAND}.",
        suffix=".tmp",
        dir=str(launcher.parent),
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(TARGET_RELATIVE_QWEN_LAUNCHER)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(0o700)
        os.replace(temp_path, launcher)
    finally:
        if path_exists_no_follow(temp_path):
            cleanup_path(temp_path)
    require_safe_executable(launcher, root, "target-relative Qwen Code launcher")


def package_metadata(root: Path) -> dict[str, Any]:
    metadata = load_json_object(root / SOFTWARE_DIR_RELATIVE / "package.json", "Qwen Code package")
    if metadata.get("name") != QWEN_CODE_PACKAGE:
        fail("Qwen Code package identity is invalid")
    version = metadata.get("version")
    if not isinstance(version, str) or SEMVER_PATTERN.fullmatch(version) is None:
        fail("Qwen Code package version is invalid")
    return metadata


def digest_regular_file(
    path: Path,
    label: str,
    byte_counter: dict[str, int],
) -> str:
    before = path.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        fail(f"{label} must be a regular file")
    if before.st_nlink != 1:
        fail(f"{label} must not have hard-link aliases")
    if hasattr(os, "geteuid") and owner_of(before) != os.geteuid():
        fail(f"{label} must be owned by the current user")
    mode = stat.S_IMODE(before.st_mode)
    if mode not in {OWNER_FILE_MODE, 0o700}:
        fail(f"{label} must be private to the current user")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    try:
        opened = os.fstat(descriptor)
        if identity_of(opened) != identity_of(before):
            fail_concurrent(f"{label} changed while it was being opened")
        if hasattr(os, "geteuid") and owner_of(opened) != os.geteuid():
            fail(f"{label} must be owned by the current user")
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            byte_counter["value"] += len(chunk)
            if byte_counter["value"] > SOFTWARE_TREE_MAX_BYTES:
                fail(f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_BYTES}-byte limit")
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = path.lstat()
    expected = identity_of(before)
    if (
        identity_of(opened) != expected
        or identity_of(after) != expected
        or identity_of(final) != expected
    ):
        fail_concurrent(f"{label} changed while it was being read")
    return digest.hexdigest()


def iter_software_tree_paths(root: Path) -> list[Path]:
    paths = [Path("bin") / QWEN_COMMAND, SOFTWARE_DIR_RELATIVE]
    install_root = root / SOFTWARE_DIR_RELATIVE
    if install_root.exists() or install_root.is_symlink():
        for path in install_root.rglob("*"):
            paths.append(path.relative_to(root))
            if len(paths) > SOFTWARE_TREE_MAX_PATHS:
                fail(f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
    return sorted(set(paths), key=lambda item: str(item))


def resolve_target_owned_symlink(path: Path, root: Path, label: str) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        fail(f"{label} symlink is broken")
    try:
        resolved.relative_to(root.resolve())
    except ValueError:
        fail(f"{label} symlink must stay inside the target")
    return resolved


def digest_resolved_symlink_tree(
    path: Path,
    root: Path,
    label: str,
    byte_counter: dict[str, int],
    path_counter: dict[str, int],
    seen_directories: set[tuple[int, int]],
) -> dict[str, Any]:
    if path_counter["value"] > SOFTWARE_TREE_MAX_PATHS:
        fail(f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    mode = stat.S_IMODE(info.st_mode)
    if stat.S_ISLNK(info.st_mode):
        resolved = resolve_target_owned_symlink(path, root, label)
        nested = digest_resolved_symlink_tree(
            resolved,
            root,
            f"{label} resolved target",
            byte_counter,
            path_counter,
            seen_directories,
        )
        return {
            "type": "symlink",
            "target": os.readlink(path),
            "resolved": str(resolved.relative_to(root)),
            "resolved_target": nested,
        }
    if stat.S_ISREG(info.st_mode):
        return {
            "type": "file",
            "mode": mode,
            "size": info.st_size,
            "sha256": digest_regular_file(path, label, byte_counter),
            "owner_executable": bool(mode & stat.S_IXUSR),
        }
    if stat.S_ISDIR(info.st_mode):
        if not is_owner_private_directory(info):
            fail(f"{label} directory must be private to the current user")
        directory_identity = identity_of(info)
        if directory_identity in seen_directories:
            fail(f"{label} resolves to a directory cycle")
        seen_directories.add(directory_identity)
        children: list[dict[str, Any]] = []
        try:
            entries = sorted(path.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            fail(f"cannot enumerate {label}: {exc}")
        for child in entries:
            path_counter["value"] += 1
            if path_counter["value"] > SOFTWARE_TREE_MAX_PATHS:
                fail(
                    f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit"
                )
            children.append(
                {
                    "name": child.name,
                    **digest_resolved_symlink_tree(
                        child,
                        root,
                        f"{label}/{child.name}",
                        byte_counter,
                        path_counter,
                        seen_directories,
                    ),
                }
            )
        seen_directories.remove(directory_identity)
        return {
            "type": "directory",
            "mode": mode,
            "tree_digest": sha256_bytes(canonical_json(children)),
            "tree_paths": len(children),
        }
    fail(f"{label} resolved target has an unsupported file type")


def validate_software_symlink(
    path: Path,
    root: Path,
    label: str,
    byte_counter: dict[str, int],
) -> dict[str, Any]:
    resolved = resolve_target_owned_symlink(path, root, label)
    path_counter = {"value": 1}
    target_record = digest_resolved_symlink_tree(
        resolved,
        root,
        f"{label} resolved target",
        byte_counter,
        path_counter,
        set(),
    )
    return {
        "path": str(path.relative_to(root)),
        "type": "symlink",
        "target": os.readlink(path),
        "resolved": str(resolved.relative_to(root)),
        "resolved_target": target_record,
        "resolved_tree_paths": path_counter["value"],
    }


def compute_software_tree_digest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    byte_counter = {"value": 0}
    records: list[dict[str, Any]] = []
    for relative in iter_software_tree_paths(root):
        if len(records) >= SOFTWARE_TREE_MAX_PATHS:
            fail(f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
        path = root / relative
        try:
            info = path.lstat()
        except FileNotFoundError:
            fail(f"installed software path {relative} is missing")
        mode = stat.S_IMODE(info.st_mode)
        if stat.S_ISDIR(info.st_mode):
            if not is_owner_private_directory(info):
                fail(f"installed software directory {relative} must be private to the current user")
            records.append({"path": str(relative), "type": "directory", "mode": mode})
            continue
        if stat.S_ISLNK(info.st_mode):
            records.append(
                validate_software_symlink(
                    path,
                    root,
                    f"installed software path {relative}",
                    byte_counter,
                )
            )
            continue
        digest = digest_regular_file(path, f"installed software file {relative}", byte_counter)
        records.append(
            {
                "path": str(relative),
                "type": "file",
                "mode": mode,
                "size": info.st_size,
                "sha256": digest,
                "owner_executable": bool(mode & stat.S_IXUSR),
            }
        )
    require_safe_executable(root / "bin" / QWEN_COMMAND, root, "Qwen Code executable")
    metadata = package_metadata(root)
    return {
        "tree_digest": sha256_bytes(canonical_json(records)),
        "tree_bytes": byte_counter["value"],
        "tree_paths": len(records),
        "package_name": metadata["name"],
        "version": metadata["version"],
        "entrypoint_sha256": digest_regular_file(
            root / "bin" / QWEN_COMMAND,
            "Qwen Code executable",
            {"value": 0},
        ),
        "package_manifest_sha256": digest_regular_file(
            root / SOFTWARE_DIR_RELATIVE / "package.json",
            "Qwen Code package manifest",
            {"value": 0},
        ),
    }


def software_manifest_identity() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "package": QWEN_CODE_PACKAGE,
        "install_method": "official-standalone",
        "installer_url": INSTALLER_URL,
        "installer_sha256": INSTALLER_SHA256,
        "installer_argv": list(INSTALLER_ARGV),
        "archive_verification": "official SHA256SUMS",
        "executable": f"bin/{QWEN_COMMAND}",
        "entrypoint_resolution": "target-relative-wrapper",
        "install_root": str(SOFTWARE_DIR_RELATIVE),
    }


def build_software_manifest(root: Path) -> dict[str, Any]:
    return {**software_manifest_identity(), **compute_software_tree_digest(root)}


def software_presence(target: Path) -> dict[str, Any]:
    replace_paths_present = [
        str(relative) for relative in SOFTWARE_REPLACE_PATHS if path_exists_no_follow(target / relative)
    ]
    owned_parent_paths_present = [
        str(relative) for relative in SOFTWARE_PARENT_PATHS if path_exists_no_follow(target / relative)
    ]
    if not replace_paths_present and not owned_parent_paths_present:
        state = "absent"
    elif len(replace_paths_present) == len(SOFTWARE_REPLACE_PATHS):
        state = "installed"
    else:
        state = "partial"
    return {
        "software_state": state,
        "partial": state == "partial",
        "replace_paths_present": replace_paths_present,
        "owned_parent_paths_present": owned_parent_paths_present,
    }


def software_status(target: Path) -> dict[str, Any]:
    if not require_private_target_directory_for_software(target, allow_missing=True):
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": False,
            "current": False,
            "version": None,
            "executable": None,
            "software_state": "absent",
            "partial": False,
            "replace_paths_present": [],
            "owned_parent_paths_present": [],
        }
    presence = software_presence(target)
    executable = qwen_executable(target)
    if presence["software_state"] != "installed":
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": False,
            "current": False,
            "version": None,
            "executable": str(executable),
            **presence,
        }
    manifest_path = software_manifest_path(target)
    try:
        manifest = load_json_object(manifest_path, "Qwen Code software manifest", owner_only=True)
    except QwenCodeSetupError as exc:
        return {
            "schema_version": 1,
            "command": "software-status",
            "target": str(target),
            "installed": True,
            "current": False,
            "version": None,
            "executable": str(executable),
            **presence,
            "validation_error": str(exc),
        }
    try:
        expected = build_software_manifest(target)
    except QwenCodeSetupError as exc:
        expected = None
        validation_error = str(exc)
    else:
        validation_error = None
    current = (
        expected is not None
        and manifest == expected
        and manifest.get("version") == TESTED_QWEN_CODE_VERSION
    )
    result = {
        "schema_version": 1,
        "command": "software-status",
        "target": str(target),
        "installed": True,
        "current": current,
        "version": manifest.get("version"),
        "executable": str(executable),
        "package": manifest.get("package"),
        "install_method": manifest.get("install_method"),
        **presence,
    }
    if validation_error is not None:
        result["validation_error"] = validation_error
    return result


def require_current_software(target: Path) -> dict[str, Any]:
    status = software_status(target)
    if not status["installed"]:
        fail("Qwen Code CLI is not installed at the selected target; run install-cli")
    if not status["current"]:
        detail = f": {status['validation_error']}" if "validation_error" in status else ""
        fail(
            f"Qwen Code CLI is not current at the selected target; "
            f"run update-cli to install {TESTED_QWEN_CODE_VERSION}{detail}"
        )
    return status


def bounded_process(
    command: list[str], *, cwd: Path, env: dict[str, str], timeout: int
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError:
        fail(f"process executable not found: {command[0]}")
    except subprocess.TimeoutExpired:
        fail(f"process timed out after {timeout} seconds: {command[0]}")
    if (
        len(completed.stdout) > PROCESS_OUTPUT_MAX_BYTES
        or len(completed.stderr) > PROCESS_OUTPUT_MAX_BYTES
    ):
        fail(f"process output exceeded {PROCESS_OUTPUT_MAX_BYTES}-byte limit: {command[0]}")
    return completed


def validate_software_parent_destination(target: Path, relative: Path) -> None:
    parent = target / relative
    if not path_exists_no_follow(parent):
        return
    info = require_directory(parent, f"existing software parent {relative}")
    if not is_owner_private_directory(info):
        fail(f"existing software parent {relative} must be private to the current user")


def validate_replace_destination(target: Path, relative: Path) -> None:
    destination = target / relative
    if not path_exists_no_follow(destination):
        return
    if relative == Path("bin") / QWEN_COMMAND:
        require_safe_executable(destination, target, "existing Qwen Code executable")
        return
    if relative == SOFTWARE_DIR_RELATIVE:
        info = require_directory(destination, f"existing software directory {relative}")
        if not is_owner_private_directory(info):
            fail(f"existing software directory {relative} must be private to the current user")
        return
    if relative == SOFTWARE_MANIFEST_RELATIVE:
        require_regular_file(
            destination,
            f"existing software manifest {relative}",
            owner_only=True,
        )
        return
    fail(f"unsupported software replace path: {relative}")


def validate_existing_software_tree_safety(target: Path) -> None:
    install_root = target / SOFTWARE_DIR_RELATIVE
    if not path_exists_no_follow(install_root):
        return
    byte_counter = {"value": 0}
    for relative in iter_software_tree_paths(target):
        path = target / relative
        if not path_exists_no_follow(path):
            continue
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            if not is_owner_private_directory(info):
                fail(f"existing software directory {relative} must be private to the current user")
            continue
        if stat.S_ISLNK(info.st_mode):
            validate_software_symlink(
                path,
                target,
                f"existing software path {relative}",
                byte_counter,
            )
            continue
        digest_regular_file(path, f"existing software file {relative}", byte_counter)


def validate_existing_software_surface(target: Path) -> None:
    for relative in SOFTWARE_PARENT_PATHS:
        validate_software_parent_destination(target, relative)
    for relative in SOFTWARE_REPLACE_PATHS:
        validate_replace_destination(target, relative)
    validate_existing_software_tree_safety(target)


def ensure_replace_parent(destination: Path) -> None:
    parent = destination.parent
    try:
        info = parent.lstat()
    except FileNotFoundError:
        parent.mkdir(mode=OWNER_DIRECTORY_MODE, parents=True)
        parent.chmod(OWNER_DIRECTORY_MODE)
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"software destination parent {parent} must be a real directory")
    if not is_owner_private_directory(info):
        fail(f"software destination parent {parent} must be private to the current user")


def move_replace_path(source: Path, destination: Path) -> None:
    ensure_replace_parent(destination)
    os.replace(source, destination)


def move_old_path(source: Path, saved: Path) -> None:
    saved.parent.mkdir(mode=OWNER_DIRECTORY_MODE, parents=True, exist_ok=True)
    os.replace(source, saved)


def cleanup_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def restore_software_paths(
    target: Path,
    hold: Path,
    live_stage: Path,
    *,
    moved_old: list[Path],
    installed_new: list[Path],
    preexisting_parent_paths: set[Path],
) -> None:
    new_paths = set(installed_new)
    for relative in SOFTWARE_REPLACE_PATHS:
        if relative not in new_paths and not path_exists_no_follow(live_stage / relative):
            new_paths.add(relative)
    for relative in reversed(SOFTWARE_REPLACE_PATHS):
        destination = target / relative
        if relative in new_paths and path_exists_no_follow(destination):
            cleanup_path(destination)
    for relative in reversed(moved_old):
        saved = hold / relative
        if not path_exists_no_follow(saved):
            continue
        move_replace_path(saved, target / relative)
    for relative in sorted(SOFTWARE_PARENT_PATHS, key=lambda item: len(item.parts), reverse=True):
        if relative in preexisting_parent_paths:
            continue
        parent = target / relative
        if not parent.exists() or parent.is_symlink() or not parent.is_dir():
            continue
        try:
            parent.rmdir()
        except OSError:
            continue


def replace_software_state(target: Path, live_stage: Path, hold_parent: Path) -> None:
    for relative in SOFTWARE_REPLACE_PATHS:
        source = live_stage / relative
        if not path_exists_no_follow(source):
            fail(f"staged software path {relative} is missing")
        validate_replace_destination(live_stage, relative)
        validate_replace_destination(target, relative)
    hold = hold_parent / "rollback"
    if path_exists_no_follow(hold):
        cleanup_path(hold)
    hold.mkdir(mode=OWNER_DIRECTORY_MODE)
    preexisting_parent_paths = {
        relative for relative in SOFTWARE_PARENT_PATHS if path_exists_no_follow(target / relative)
    }
    moved_old: list[Path] = []
    installed_new: list[Path] = []
    try:
        for relative in SOFTWARE_REPLACE_PATHS:
            destination = target / relative
            if path_exists_no_follow(destination):
                saved = hold / relative
                move_old_path(destination, saved)
                moved_old.append(relative)
        for relative in SOFTWARE_REPLACE_PATHS:
            move_replace_path(live_stage / relative, target / relative)
            installed_new.append(relative)
        status = software_status(target)
        if not status["installed"] or not status["current"]:
            fail("installed Qwen Code CLI did not validate as the tested standalone version")
    except BaseException:
        moved_old = [
            relative for relative in SOFTWARE_REPLACE_PATHS if path_exists_no_follow(hold / relative)
        ]
        restore_software_paths(
            target,
            hold,
            live_stage,
            moved_old=moved_old,
            installed_new=installed_new,
            preexisting_parent_paths=preexisting_parent_paths,
        )
        raise
    finally:
        shutil.rmtree(hold, ignore_errors=True)


def run_official_standalone_installer(stage_root: Path, live_stage: Path) -> None:
    installer = download_official_installer(stage_root)
    env = install_stage_environment(stage_root, live_stage)
    completed = bounded_process(
        ["/bin/bash", str(installer), *INSTALLER_ARGV],
        cwd=stage_root,
        env=env,
        timeout=PROCESS_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        fail(
            "official Qwen Code standalone installer failed with exit "
            f"{completed.returncode}: {completed.stderr.strip()}"
        )
    chmod_private_tree(live_stage)
    write_target_relative_qwen_launcher(live_stage)
    observed = observed_qwen_version(live_stage / "bin" / QWEN_COMMAND, live_stage)
    if observed != TESTED_QWEN_CODE_VERSION:
        fail(f"installer produced Qwen Code {observed}, expected {TESTED_QWEN_CODE_VERSION}")


def write_stage_software_manifest(live_stage: Path) -> None:
    manifest = live_stage / SOFTWARE_MANIFEST_RELATIVE
    manifest.parent.mkdir(mode=OWNER_DIRECTORY_MODE, parents=True, exist_ok=True)
    manifest.write_bytes(canonical_json(build_software_manifest(live_stage)))
    manifest.chmod(OWNER_FILE_MODE)


def install_or_update_cli(target: Path, command: str) -> dict[str, Any]:
    target_existed_before = path_exists_no_follow(target)
    preflight = software_status(target)
    if command == "install-cli":
        if preflight.get("partial"):
            fail(
                "partial target-owned Qwen Code software state exists; "
                "use update-cli or repair/remove the target-owned software paths"
            )
        if preflight.get("replace_paths_present") or preflight.get("owned_parent_paths_present"):
            fail("Qwen Code CLI software already exists; use update-cli")
    if command == "update-cli":
        if preflight["software_state"] == "absent":
            fail("Qwen Code CLI is not installed at the selected target; use install-cli")
        validate_existing_software_surface(target)
        if preflight["installed"] and preflight["current"]:
            return {
                "schema_version": 1,
                "command": command,
                "target": str(target),
                "changed": False,
                "version": preflight["version"],
                "executable": preflight["executable"],
            }
    staging: Path | None = None
    with target_lock(target):
        try:
            ensure_private_directory(target, create=True)
            status = software_status(target)
            if command == "install-cli":
                if status.get("partial"):
                    fail(
                        "partial target-owned Qwen Code software state exists; "
                        "use update-cli or repair/remove the target-owned software paths"
                    )
                if status.get("replace_paths_present") or status.get("owned_parent_paths_present"):
                    fail("Qwen Code CLI software already exists; use update-cli")
            if command == "update-cli":
                if status["software_state"] == "absent":
                    fail("Qwen Code CLI is not installed at the selected target; use install-cli")
                validate_existing_software_surface(target)
                if status["installed"] and status["current"]:
                    return {
                        "schema_version": 1,
                        "command": command,
                        "target": str(target),
                        "changed": False,
                        "version": status["version"],
                        "executable": status["executable"],
                    }
            staging = Path(
                tempfile.mkdtemp(
                    dir=target.parent,
                    prefix=f".{target.name}.nddev-qwen-code-cli-stage.",
                )
            )
            staging.chmod(OWNER_DIRECTORY_MODE)
            live_stage = staging / "live"
            live_stage.mkdir(mode=OWNER_DIRECTORY_MODE)
            run_official_standalone_installer(staging, live_stage)
            chmod_private_tree(live_stage)
            write_stage_software_manifest(live_stage)
            replace_software_state(target, live_stage, staging)
            installation = require_current_software(target)
        except BaseException:
            remove_created_target_if_empty(target, target_existed_before)
            raise
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
    return {
        "schema_version": 1,
        "command": command,
        "target": str(target),
        "changed": True,
        "version": installation["version"],
        "executable": installation["executable"],
    }


def builder_status(target: Path) -> dict[str, Any]:
    extension = target / "extensions" / "nddev-builder" / "qwen-extension.json"
    status = inspect_target(target)
    installed = extension.is_file() and not extension.is_symlink()
    version = None
    if installed:
        version = load_json_object(extension, "builder extension manifest").get("version")
    return {
        "schema_version": 1,
        "command": "builder-status",
        "target": str(target),
        "installed": installed,
        "default_on": status["state"] == "managed" and status["builder_extension"] == "present",
        "version": version,
        "projection": "qwen-extension",
        "manifest": "extensions/nddev-builder/qwen-extension.json",
    }


def spawn_qwen_child(executable: str, child_args: list[str], environment: dict[str, str]) -> int:
    try:
        completed = subprocess.run([executable, *child_args], env=environment, check=False)
    except FileNotFoundError:
        fail("qwen executable disappeared before launch")
    if completed.returncode < 0:
        return 128 + abs(completed.returncode)
    return completed.returncode


def first_qwen_scope_override(child_args: list[str]) -> str | None:
    """Return the first official Qwen flag that would override managed scope."""
    for argument in child_args:
        if argument == "--":
            return None
        if argument in QWEN_SCOPE_FLAGS_WITHOUT_VALUE or argument in QWEN_SCOPE_FLAGS_WITH_VALUE:
            return argument
        if argument.startswith("--"):
            flag = argument.split("=", 1)[0]
            if flag in QWEN_SCOPE_FLAGS_WITH_VALUE and "=" in argument:
                return flag
    return None


def launch_qwen(target: Path, child_args: list[str]) -> int:
    forwarded = child_args[1:] if child_args[:1] == ["--"] else child_args
    override = first_qwen_scope_override(forwarded)
    if override is not None:
        fail(f"launch arguments must not override target-owned Qwen Code scope: {override}")
    with target_lock(target):
        require_clean_managed(target)
        installation = require_current_software(target)
        environment = launch_environment(target)
        executable = str(installation["executable"])
    return spawn_qwen_child(executable, forwarded, environment)


def human_output(value: dict[str, Any]) -> str:
    command = value.get("command")
    if command == "list":
        return "\n".join(f"{item['id']}: {item['description']}" for item in value["setups"])
    if command == "status":
        setup = f" ({value['setup_id']})" if value["setup_id"] else ""
        drift = f"; drift={','.join(value['drift'])}" if value["drift"] else ""
        return f"{value['state']}{setup}: {value['target']}{drift}"
    if command == "plan":
        changes = ", ".join(value["changes"]) or "none"
        return f"{value['operation']} {value['setup_id']} at {value['target']}; changes: {changes}"
    if command == "software-status":
        return (
            f"installed={value['installed']} current={value['current']} version={value['version']}"
        )
    if command == "builder-status":
        return (
            f"installed={value['installed']} default_on={value['default_on']} "
            f"version={value['version']} projection={value['projection']}"
        )
    return json.dumps(value, indent=2, sort_keys=True)


def add_target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", required=True, help="Absolute Qwen Code home path.")
    parser.add_argument("--json", action="store_true", dest="output_json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nddev-qwen-code",
        description="Manage a portable Qwen Code setup at an explicit target.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List source setups.")
    list_parser.add_argument("--json", action="store_true", dest="output_json")

    status_parser = subparsers.add_parser("status", help="Inspect an explicit target.")
    add_target(status_parser)

    for command in ("plan", "install", "switch"):
        command_parser = subparsers.add_parser(command, help=f"{command.title()} a setup.")
        command_parser.add_argument("--setup", required=True)
        add_target(command_parser)

    restore_parser = subparsers.add_parser("restore", help="Restore a target-bound backup.")
    restore_parser.add_argument("--backup", required=True, type=int, choices=range(10))
    add_target(restore_parser)

    remove_parser = subparsers.add_parser("remove", help="Remove only managed setup files.")
    add_target(remove_parser)

    for command, help_text in (
        ("builder-status", "Inspect the native nddev-builder Qwen extension."),
        ("software-status", "Inspect target-owned Qwen Code CLI software."),
        ("install-cli", "Install the pinned official Qwen Code standalone build."),
        ("update-cli", "Update target-owned Qwen Code to the pinned standalone build."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        add_target(command_parser)

    launch_parser = subparsers.add_parser(
        "launch", help="Launch Qwen Code with isolated QWEN_HOME."
    )
    add_target(launch_parser)
    launch_parser.add_argument(
        "qwen_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to Qwen Code after --.",
    )
    return parser


def run(args: argparse.Namespace) -> dict[str, Any] | int:
    if args.command == "list":
        return {"schema_version": 1, "command": "list", "setups": list_setups()}
    target = resolve_target(args.target)
    if args.command == "status":
        return {
            "schema_version": 1,
            "command": "status",
            "target": str(target),
            **inspect_target(target),
        }
    if args.command == "plan":
        return plan_setup(target, args.setup)
    if args.command in {"install", "switch"}:
        return mutate_setup(target, args.setup, args.command)
    if args.command == "restore":
        return restore_slot(target, args.backup)
    if args.command == "remove":
        return remove_setup(target)
    if args.command == "builder-status":
        return builder_status(target)
    if args.command == "software-status":
        return software_status(target)
    if args.command in {"install-cli", "update-cli"}:
        return install_or_update_cli(target, args.command)
    if args.command == "launch":
        return launch_qwen(target, list(args.qwen_args))
    fail(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (QwenCodeSetupError, OSError, subprocess.SubprocessError) as exc:
        if isinstance(exc, OSError):
            detail = exc.strerror or type(exc).__name__
            error_message = f"filesystem operation failed: {detail}"
            if exc.filename is not None:
                error_message += f" ({exc.filename})"
        else:
            error_message = str(exc)
        if getattr(args, "output_json", False):
            print(json.dumps({"schema_version": 1, "error": error_message}, sort_keys=True))
        else:
            print(f"nddev-qwen-code: error: {error_message}", file=sys.stderr)
        return 2
    if isinstance(result, int):
        return result
    if getattr(args, "output_json", False):
        sys.stdout.buffer.write(canonical_json(result))
    else:
        print(human_output(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
