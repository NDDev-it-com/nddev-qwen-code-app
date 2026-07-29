#!/usr/bin/env python3
"""Transactional setup manager for a caller-selected Qwen Code home."""

from __future__ import annotations

import argparse
import base64
import contextlib
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

ROOT = Path(__file__).resolve().parents[1]
CATALOG_ROOT = ROOT / "setups"
PROFILE_ROOT = ROOT / "profiles"
BUILDER_ROOT = ROOT / "extensions" / "nddev-builder"
VERSION = (ROOT / "VERSION").read_text(encoding="ascii").strip()
PRODUCT_NAME = "nddev-qwen-code-app"
STAMP_NAME = "NDDEV-QWEN-CODE-SETUP.json"
BACKUP_NAME = "NDDEV-QWEN-CODE-BACKUP.json"
MANAGED_FILES = ("settings.json", "QWEN.md", "AGENTS.md", ".claude/CLAUDE.md")
LEGACY_SCHEMA1_MANAGED_FILES = ("settings.json", "QWEN.md", "AGENTS.md", "CLAUDE.md")
BUILDER_FILES = (
    "extensions/nddev-builder/qwen-extension.json",
    "extensions/nddev-builder/QWEN.md",
    "extensions/nddev-builder/skills/qwen-builder-orientation/SKILL.md",
    "extensions/nddev-builder/agents/qwen-builder-reviewer.md",
)
CURRENT_PAYLOAD_PATHS = (*MANAGED_FILES, *BUILDER_FILES)
LEGACY_SCHEMA1_PAYLOAD_PATHS = (*LEGACY_SCHEMA1_MANAGED_FILES, *BUILDER_FILES)
ALL_PAYLOAD_PATHS = tuple(dict.fromkeys((*CURRENT_PAYLOAD_PATHS, *LEGACY_SCHEMA1_PAYLOAD_PATHS)))
MANAGED_PATHS = (*ALL_PAYLOAD_PATHS, STAMP_NAME)
BUILDER_PROJECTION = {
    "type": "qwen-extension",
    "root": "extensions/nddev-builder",
    "default_on": True,
}
OWNER_FILE_MODE = 0o600
OWNER_DIRECTORY_MODE = 0o700
METADATA_MAX_BYTES = 256 * 1024
MANAGED_PAYLOAD_MAX_BYTES = 8 * 1024 * 1024
PROCESS_OUTPUT_MAX_BYTES = 256 * 1024
PROCESS_TIMEOUT_SECONDS = 120
TESTED_QWEN_CODE_VERSION = "0.21.1"
QWEN_CODE_PACKAGE = "@qwen-code/qwen-code"
QWEN_COMMAND = "qwen"
QWEN_NPM_TARBALL_URL = "https://registry.npmjs.org/@qwen-code/qwen-code/-/qwen-code-0.21.1.tgz"
QWEN_NPM_TARBALL_SIZE_BYTES = 23836955
QWEN_NPM_INTEGRITY = "sha512-UTBegRxy3Sy5PbxyVjezHb/pNp24qxrgUnq8V0cNrnlldkvI8iB3/4N3akwhEI3nAFC3Lu1cNPxIV/gIK9L3uw=="
QWEN_NPM_SHASUM = "1d3a8426f6a4ed76ca9cd642e9adc59541973e2d"
QWEN_RELEASE_BASE_URL = "https://github.com/QwenLM/qwen-code/releases/download/v0.21.1"
QWEN_RELEASE_ARCHIVES: dict[str, dict[str, Any]] = {
    "darwin-arm64": {
        "asset": "qwen-code-darwin-arm64.tar.gz",
        "size_bytes": 75117454,
        "sha256": "98b12dd4ffbc41c205b01724d07d502311340cd3c9b2fc5fbf6ca0dbcc0d82b6",
    },
    "darwin-x64": {
        "asset": "qwen-code-darwin-x64.tar.gz",
        "size_bytes": 76413403,
        "sha256": "b7696885bfb1daacbf6433309079121212d0576728745f47f98c3eabe1d5e92e",
    },
    "linux-arm64": {
        "asset": "qwen-code-linux-arm64.tar.gz",
        "size_bytes": 82000362,
        "sha256": "01d664ea21465bf649ce246d8328ed93b88a00d4a87d3db54a4e608b8bbaf454",
    },
    "linux-x64": {
        "asset": "qwen-code-linux-x64.tar.gz",
        "size_bytes": 82210013,
        "sha256": "30fd2b411c05ec551bcba729862fc41adc0ecbe1492e956d007e3fa38349bb1c",
    },
}
CONTROLLED_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
NPM_TARBALL_MAX_BYTES = 64 * 1024 * 1024
RELEASE_ARCHIVE_MAX_BYTES = 128 * 1024 * 1024
SOFTWARE_TREE_MAX_BYTES = 1024 * 1024 * 1024
SOFTWARE_TREE_MAX_PATHS = 100000
CONTROL_ROOT_RELATIVE = Path(".nddev") / "qwen-code"
PRODUCT_ANCHOR_NAME = "product.lock"
TARGET_ANCHOR_DIRECTORY = "targets"
TARGET_ANCHOR_SUFFIX = ".lock"
ANCHOR_PUBLICATION_PREFIX = ".nddev-qwen-code-publish-"
ANCHOR_STAGE_CANDIDATE_LIMIT = 8
CLEANUP_DIRECTORY_NAME = "cleanup"
CLEANUP_PREPARE_NAME = "prepare.json"
CLEANUP_JOURNAL_NAME = "pending.json"
CLEANUP_TOMBSTONE_DIRECTORY = "tombstones"
CLEANUP_MAX_TREE_ENTRIES = 2048
CLEANUP_MAX_SERIALIZED_BYTES = 1024 * 1024
RECOVERY_ROOT_SUFFIX = ".nddev-qwen-code-recovery"
RECOVERY_MANIFEST_NAME = "manifest.json"
RECOVERY_COMMIT_NAME = "committed.json"
RECOVERY_HOLD_DIRECTORY = "hold"
RECOVERY_STAGE_DIRECTORY = "stage"
RECOVERY_MAX_OPERATIONS = 64
RECOVERY_MAX_GRAPH_ENTRIES = SOFTWARE_TREE_MAX_PATHS if "SOFTWARE_TREE_MAX_PATHS" in globals() else 100000
RECOVERY_MAX_SERIALIZED_BYTES = 4 * 1024 * 1024
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
CONTENT_SETUP_ID = "nddev-builder"
DEFAULT_PROFILE_ID = "full-auto"
PROFILE_IDS = ("full-auto", "safe")
LEGACY_SETUP_PROFILES = {
    "safe": "safe",
    "balanced": "balanced",
    "full-auto": "full-auto",
}
PROFILE_TOOL_POLICY = {
    "full-auto": ("yolo", False),
    "safe": ("default", True),
}
LEGACY_PROFILE_TOOL_POLICY = {
    **PROFILE_TOOL_POLICY,
    "balanced": ("auto-edit", True),
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
SEMVER_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)
STAMP_V1_KEYS = {
    "schema_version",
    "product_name",
    "build_version",
    "setup_id",
    "canonical_target",
    "managed_paths",
}
STAMP_V2_KEYS = STAMP_V1_KEYS | {"profile_id", "builder_projection"}
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
BACKUP_RECORD_KEYS = {"path", "size", "sha256"}
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
    "--addDir",
    "--cwd",
    "--include-directories",
    "--includeDirectories",
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
    "--workspace",
    "--worktree",
    "-e",
    "-i",
    "-m",
    "-o",
    "-p",
    "-r",
}
QWEN_SCOPE_SHORT_FLAGS_WITH_VALUE = {"-e", "-i", "-m", "-o", "-p", "-r"}
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


class CleanupPending(QwenCodeSetupError):
    """A valid post-commit cleanup state remains."""


class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that emits one JSON error when --json is requested."""

    def error(self, message: str) -> NoReturn:
        if "--json" in sys.argv[1:]:
            print(json.dumps({"schema_version": 1, "error": message}, sort_keys=True))
            raise SystemExit(2)
        super().error(message)


@dataclass(frozen=True)
class FileSnapshot:
    digest: str
    mode: int
    inode: tuple[int, int]
    owner: int | None


@dataclass(frozen=True)
class LaunchImage:
    root: Path
    executable: Path
    digest: str
    inode: tuple[int, int]


@dataclass(frozen=True)
class HostModel:
    product_host_id: str
    vendor_os: str
    vendor_arch: str
    unsupported_category: str | None = None


@dataclass(frozen=True)
class ColdNamespaceSnapshot:
    state: str
    root_identity: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class DirectoryMetadataSnapshot:
    path: Path
    identity: tuple[Any, ...]
    mode: int
    atime_ns: int
    mtime_ns: int


@dataclass
class CreatedDirectorySignature:
    path: Path
    fd: int | None
    parent: DirectoryMetadataSnapshot
    identity: tuple[int, int]
    file_type: int
    uid: int | None
    gid: int | None
    mode: int
    nlink: int


@dataclass(frozen=True)
class AnchorStage:
    path: Path
    identity: tuple[int, int]
    content: bytes
    parent_snapshot: DirectoryMetadataSnapshot | None = None
    created_parent: CreatedDirectorySignature | None = None


@dataclass
class HeldGraph:
    root: Path
    records: list[dict[str, Any]]
    descriptors: dict[str, int]


@dataclass
class RecoveryOperation:
    anchor: str
    relative: Path
    before: HeldGraph | None
    after: HeldGraph | None
    hold_relative: Path
    stage_relative: Path


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


def mode_of(info: os.stat_result) -> int:
    return stat.S_IMODE(info.st_mode)


def current_uid() -> int | None:
    return os.geteuid() if hasattr(os, "geteuid") else None


def parse_os_release(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def normalize_architecture(machine: str) -> str | None:
    normalized = machine.lower()
    if normalized in {"arm64", "aarch64"}:
        return "arm64"
    if normalized in {"x86_64", "amd64"}:
        return "x64"
    return None


def classify_supported_host(
    *,
    system_name: str | None = None,
    machine_name: str | None = None,
    os_release_content: str | None = None,
    libc_name: str | None = None,
) -> HostModel:
    system_value = system_name or platform.system()
    arch = normalize_architecture(machine_name or platform.machine())
    if arch is None:
        return HostModel("", "", "", "unsupported-architecture")
    if system_value == "Darwin":
        return HostModel(f"macos-{arch}", "darwin", arch)
    if system_value != "Linux":
        category = "windows" if system_value == "Windows" else "unsupported-host"
        return HostModel("", "", "", category)
    if os_release_content is None:
        try:
            os_release_content = Path("/etc/os-release").read_text(encoding="utf-8")
        except OSError:
            os_release_content = ""
    os_release = parse_os_release(os_release_content)
    if os_release.get("ID") != "ubuntu":
        return HostModel("", "", "", "non-ubuntu-linux")
    libc_value = libc_name if libc_name is not None else platform.libc_ver()[0]
    if libc_value != "glibc":
        return HostModel("", "", "", "linux-musl")
    return HostModel(f"ubuntu-glibc-{arch}", "linux", arch)


def preflight_supported_host() -> HostModel:
    model = classify_supported_host()
    if model.unsupported_category is not None:
        fail(f"unsupported host for Qwen Code lifecycle: {model.unsupported_category}")
    return model


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


def require_regular_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    allow_hardlinks: bool = False,
) -> os.stat_result:
    try:
        info = path.lstat()
    except FileNotFoundError:
        fail(f"{label} is missing")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"{label} must be a regular non-symlink file")
    if info.st_nlink != 1 and not allow_hardlinks:
        fail(f"{label} must not have hard-link aliases")
    if owner_only and not is_owner_only_file(info):
        fail(f"{label} must be owned by the current user with mode 0600")
    if info.st_size > MANAGED_PAYLOAD_MAX_BYTES:
        fail(f"{label} exceeds the {MANAGED_PAYLOAD_MAX_BYTES}-byte size limit")
    return info


def fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def open_no_follow(path: Path, flags: int, mode: int | None = None) -> int:
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if mode is None:
        return os.open(path, flags)
    return os.open(path, flags, mode)


def control_root_path() -> Path:
    if platform.system() == "Darwin":
        parent = Path("/private/tmp")
    else:
        parent = Path("/tmp")
    try:
        info = parent.lstat()
    except FileNotFoundError:
        fail(f"Qwen Code control parent is missing: {parent}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"Qwen Code control parent must be a real directory: {parent}")
    if not (info.st_mode & stat.S_ISVTX):
        fail(f"Qwen Code control parent must be sticky: {parent}")
    uid = current_uid()
    if uid is None:
        fail("Qwen Code control root requires a numeric local user id")
    return parent / f".{PRODUCT_NAME}.uid-{uid}"


def open_directory_no_follow(path: Path) -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return os.open(path, flags)


def created_directory_signature(
    path: Path,
    fd: int,
    parent: DirectoryMetadataSnapshot,
    label: str,
) -> CreatedDirectorySignature:
    opened = os.fstat(fd)
    current = require_directory(path, label)
    if identity_of(opened) != identity_of(current):
        fail_concurrent(f"{label} changed before it could be bound")
    if not stat.S_ISDIR(opened.st_mode) or not is_owner_private_directory(opened):
        fail(f"{label} must be a private manager-owned directory")
    return CreatedDirectorySignature(
        path=path,
        fd=fd,
        parent=parent,
        identity=identity_of(opened),
        file_type=stat.S_IFMT(opened.st_mode),
        uid=owner_of(opened),
        gid=opened.st_gid if hasattr(opened, "st_gid") else None,
        mode=stat.S_IMODE(opened.st_mode),
        nlink=opened.st_nlink,
    )


def close_created_directory_signature(signature: CreatedDirectorySignature | None) -> None:
    if signature is None or signature.fd is None:
        return
    fd = signature.fd
    signature.fd = None
    os.close(fd)


def created_directory_signature_matches(
    signature: CreatedDirectorySignature,
    info: os.stat_result,
) -> bool:
    gid = info.st_gid if hasattr(info, "st_gid") else None
    return (
        identity_of(info) == signature.identity
        and stat.S_IFMT(info.st_mode) == signature.file_type
        and owner_of(info) == signature.uid
        and gid == signature.gid
        and stat.S_IMODE(info.st_mode) == signature.mode
        and info.st_nlink == signature.nlink
    )


def validate_created_directory_signature(
    signature: CreatedDirectorySignature,
    label: str,
) -> os.stat_result:
    if signature.fd is None:
        fail(f"{label} creation signature is closed")
    opened = os.fstat(signature.fd)
    if not created_directory_signature_matches(signature, opened):
        fail(f"{label} held directory signature changed")
    current = require_directory(signature.path, label)
    if not created_directory_signature_matches(signature, current):
        fail(f"{label} path was replaced before rollback")
    return current


def ensure_private_directory_component_held(path: Path) -> CreatedDirectorySignature | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        parent = snapshot_directory_metadata(path.parent, f"{path.name} parent")
        signature: CreatedDirectorySignature | None = None
        fd: int | None = None
        try:
            path.mkdir(mode=OWNER_DIRECTORY_MODE)
            fd = open_directory_no_follow(path)
            os.fchmod(fd, OWNER_DIRECTORY_MODE)
            signature = created_directory_signature(path, fd, parent, f"private directory {path}")
            fd = None
            fsync_directory(path.parent)
            return signature
        except BaseException:
            if signature is not None:
                rollback_created_directory(signature, f"private directory {path}")
            elif fd is not None:
                os.close(fd)
            raise
    if not is_owner_private_directory(info):
        fail(f"{path} must be a private manager-owned directory")
    return None


def ensure_private_directory_component(path: Path) -> None:
    signature = ensure_private_directory_component_held(path)
    close_created_directory_signature(signature)


def require_control_root(*, create: bool) -> Path | None:
    root = control_root_path()
    try:
        info = root.lstat()
    except FileNotFoundError:
        if not create:
            return None
        ensure_private_directory_component(root)
        info = root.lstat()
    if not is_owner_private_directory(info):
        fail(f"Qwen Code control root must be a private manager-owned directory: {root}")
    return root


def product_anchor_path(root: Path) -> Path:
    return root / PRODUCT_ANCHOR_NAME


def target_digest(target: Path) -> str:
    return sha256_bytes(str(target).encode("utf-8"))


def target_anchor_path(root: Path, target: Path) -> Path:
    return root / TARGET_ANCHOR_DIRECTORY / f"{target_digest(target)}{TARGET_ANCHOR_SUFFIX}"


def cleanup_root_path(root: Path, target: Path) -> Path:
    return root / CLEANUP_DIRECTORY_NAME / target_digest(target)


def namespace_identity(info: os.stat_result) -> tuple[Any, ...]:
    return (
        info.st_dev,
        info.st_ino,
        owner_of(info),
        stat.S_IMODE(info.st_mode),
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
    )


def snapshot_directory_metadata(path: Path, label: str) -> DirectoryMetadataSnapshot:
    info = require_directory(path, label)
    return DirectoryMetadataSnapshot(
        path=path,
        identity=namespace_identity(info),
        mode=stat.S_IMODE(info.st_mode),
        atime_ns=info.st_atime_ns,
        mtime_ns=info.st_mtime_ns,
    )


def restore_directory_metadata(snapshot: DirectoryMetadataSnapshot, label: str) -> None:
    info = require_directory(snapshot.path, label)
    if stat.S_IMODE(info.st_mode) != snapshot.mode:
        os.chmod(snapshot.path, snapshot.mode)
    os.utime(snapshot.path, ns=(snapshot.atime_ns, snapshot.mtime_ns))
    final = require_directory(snapshot.path, label)
    if namespace_identity(final) != snapshot.identity:
        fail(f"{label} metadata did not restore exactly")


def rollback_created_directory(
    signature: CreatedDirectorySignature | None,
    label: str,
) -> None:
    if signature is None:
        return
    try:
        validate_created_directory_signature(signature, label)
        if list(signature.path.iterdir()):
            fail(f"{label} rollback found unexpected entries")
        signature.path.rmdir()
        fsync_directory(signature.path.parent)
        restore_directory_metadata(signature.parent, f"{label} parent")
    finally:
        close_created_directory_signature(signature)


def cold_read_namespace_snapshot(root: Path | None) -> ColdNamespaceSnapshot:
    if root is None or not path_exists_no_follow(root):
        return ColdNamespaceSnapshot("absent")
    info = require_directory(root, "Qwen Code control root")
    if not is_owner_private_directory(info):
        fail(f"Qwen Code control root must be a private manager-owned directory: {root}")
    product_anchor = product_anchor_path(root)
    if path_exists_no_follow(product_anchor):
        return ColdNamespaceSnapshot("anchored", namespace_identity(info))
    entries = sorted(entry.name for entry in root.iterdir())
    if entries:
        fail(
            "Qwen Code control namespace is not empty without a product anchor: "
            + ", ".join(entries)
        )
    return ColdNamespaceSnapshot("empty", namespace_identity(info))


def validate_cold_read_namespace(root: Path | None) -> None:
    snapshot = cold_read_namespace_snapshot(root)
    if snapshot.state == "anchored":
        return


def anchor_payload(kind: str, *, target: Path | None = None) -> bytes:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "kind": kind,
    }
    if target is not None:
        payload["canonical_target"] = str(target)
        payload["target_digest"] = target_digest(target)
    return canonical_json(payload)


def validate_anchor_content(path: Path, content: bytes, kind: str, target: Path | None) -> None:
    value = parse_json_object(content, f"coordination anchor {path}")
    expected = {"schema_version", "product_name", "kind"}
    if target is not None:
        expected |= {"canonical_target", "target_digest"}
    require_exact_keys(value, expected, f"coordination anchor {path}")
    if value["schema_version"] != 1 or value["product_name"] != PRODUCT_NAME:
        fail(f"coordination anchor {path} identity or schema is invalid")
    if value["kind"] != kind:
        fail(f"coordination anchor {path} kind mismatch")
    if target is not None and (
        value["canonical_target"] != str(target) or value["target_digest"] != target_digest(target)
    ):
        fail(f"coordination anchor {path} target binding mismatch")


def anchor_publication_prefix_for(path: Path) -> str:
    return f"{ANCHOR_PUBLICATION_PREFIX}{path.name}."


def anchor_publication_stage_path(path: Path) -> Path:
    return path.parent / (
        f"{anchor_publication_prefix_for(path)}{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )


def is_anchor_publication_stage_name(path: Path, name: str) -> bool:
    prefix = anchor_publication_prefix_for(path)
    if not name.startswith(prefix) or not name.endswith(".tmp"):
        return False
    suffix = name[len(prefix) : -len(".tmp")]
    parts = suffix.split(".")
    return (
        len(parts) == 2
        and parts[0].isdigit()
        and 1 <= len(parts[0]) <= 20
        and re.fullmatch(r"[0-9a-f]{32}", parts[1]) is not None
    )


def anchor_publication_stage_paths(path: Path) -> list[Path]:
    try:
        entries = sorted(path.parent.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        fail(f"cannot enumerate coordination namespace {path.parent}: {exc}")
    prefix = anchor_publication_prefix_for(path)
    candidates = [entry for entry in entries if entry.name.startswith(prefix)]
    if len(candidates) > ANCHOR_STAGE_CANDIDATE_LIMIT:
        fail(f"coordination anchor {path} has excessive publication stages")
    for entry in candidates:
        if not is_anchor_publication_stage_name(path, entry.name):
            fail(f"coordination anchor {path} has malformed publication stage {entry.name}")
    return candidates


def validate_anchor_publication_stage(
    stage: Path,
    path: Path,
    content: bytes,
    *,
    kind: str,
    target: Path | None,
) -> AnchorStage:
    if not is_anchor_publication_stage_name(path, stage.name):
        fail(f"coordination anchor {path} has malformed publication stage {stage.name}")
    before = require_regular_file(
        stage,
        f"coordination anchor publication stage {stage}",
        owner_only=True,
        allow_hardlinks=False,
    )
    if before.st_size != len(content):
        fail(f"coordination anchor publication stage {stage} has invalid size")
    current_content, after = read_regular_file(
        stage,
        f"coordination anchor publication stage {stage}",
        owner_only=True,
        max_bytes=METADATA_MAX_BYTES,
        allow_hardlinks=False,
    )
    if identity_of(after) != identity_of(before):
        fail_concurrent(f"coordination anchor publication stage {stage} changed")
    if current_content != content:
        fail(f"coordination anchor publication stage {stage} payload mismatch")
    validate_anchor_content(stage, current_content, kind, target)
    return AnchorStage(path=stage, identity=identity_of(after), content=current_content)


def valid_anchor_publication_stages(
    path: Path,
    content: bytes,
    *,
    kind: str,
    target: Path | None,
    final_info: os.stat_result | None = None,
) -> list[AnchorStage]:
    stages: list[AnchorStage] = []
    for stage in anchor_publication_stage_paths(path):
        try:
            info = stage.lstat()
        except FileNotFoundError:
            continue
        if final_info is not None and identity_of(info) == identity_of(final_info):
            continue
        stages.append(
            validate_anchor_publication_stage(
                stage,
                path,
                content,
                kind=kind,
                target=target,
            )
        )
    return sorted(stages, key=lambda item: item.path.name)


def ensure_no_anchor_publication_stages(path: Path) -> None:
    if not path_exists_no_follow(path.parent):
        return
    stages = anchor_publication_stage_paths(path)
    if stages:
        fail(f"coordination anchor {path} has incomplete pre-link publication stages")


def ensure_product_namespace_publishable(path: Path) -> None:
    if path.name != PRODUCT_ANCHOR_NAME:
        return
    prefix = anchor_publication_prefix_for(path)
    try:
        entries = sorted(path.parent.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        fail(f"cannot enumerate coordination namespace {path.parent}: {exc}")
    for entry in entries:
        if entry.name == path.name or entry.name.startswith(prefix):
            continue
        fail(
            "Qwen Code control namespace is not empty without a product anchor: "
            + entry.name
        )
    anchor_publication_stage_paths(path)


def publication_aliases_for(path: Path, info: os.stat_result) -> list[Path]:
    aliases: list[Path] = []
    try:
        entries = sorted(path.parent.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        fail(f"cannot enumerate coordination namespace {path.parent}: {exc}")
    prefix = anchor_publication_prefix_for(path)
    for entry in entries:
        if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
            continue
        if not is_anchor_publication_stage_name(path, entry.name):
            fail(f"coordination anchor {path} has malformed publication alias {entry.name}")
        try:
            entry_info = entry.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISREG(entry_info.st_mode) and identity_of(entry_info) == identity_of(info):
            aliases.append(entry)
    return aliases


def validate_anchor_path(
    path: Path,
    *,
    kind: str,
    target: Path | None = None,
    allow_publication_alias: bool = False,
) -> os.stat_result:
    content, info = read_regular_file(
        path,
        f"coordination anchor {path}",
        owner_only=True,
        max_bytes=METADATA_MAX_BYTES,
        allow_hardlinks=True,
    )
    validate_anchor_content(path, content, kind, target)
    if info.st_nlink != 1:
        if not allow_publication_alias:
            fail(f"coordination anchor {path} has an incomplete publication alias")
        aliases = publication_aliases_for(path, info)
        if len(aliases) != 1 or info.st_nlink != 2:
            fail(f"coordination anchor {path} has unknown hard-link aliases")
    return info


def publish_no_replace_file(
    path: Path, content: bytes, *, max_bytes: int = METADATA_MAX_BYTES
) -> None:
    if len(content) > max_bytes:
        fail(f"machine-owned file {path} exceeds the {max_bytes}-byte bound")
    ensure_private_directory_component(path.parent)
    temporary = path.parent / (
        f"{ANCHOR_PUBLICATION_PREFIX}{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    descriptor = open_no_follow(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        OWNER_FILE_MODE,
    )
    linked = False
    try:
        try:
            offset = 0
            while offset < len(content):
                written = os.write(descriptor, content[offset:])
                if written <= 0:
                    fail(f"short write while publishing {path}")
                offset += written
            os.fchmod(descriptor, OWNER_FILE_MODE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.link(temporary, path)
            linked = True
        except FileExistsError:
            temporary.unlink()
            fsync_directory(path.parent)
            return
        except OSError as exc:
            temporary.unlink()
            fsync_directory(path.parent)
            raise QwenCodeSetupError(f"coordination anchor publication failed: {exc}") from exc
        fsync_directory(path.parent)
        temporary.unlink()
        fsync_directory(path.parent)
    finally:
        if not linked and path_exists_no_follow(temporary):
            try:
                temporary.unlink()
                fsync_directory(temporary.parent)
            except OSError:
                pass


def unlink_created_stage(stage: Path, identity: tuple[int, int], parent: DirectoryMetadataSnapshot) -> None:
    try:
        info = stage.lstat()
    except FileNotFoundError:
        restore_directory_metadata(parent, f"coordination namespace {stage.parent}")
        return
    if identity_of(info) != identity:
        fail(f"coordination anchor publication stage {stage} changed before cleanup")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        fail(f"coordination anchor publication stage {stage} changed kind before cleanup")
    stage.unlink()
    fsync_directory(stage.parent)
    restore_directory_metadata(parent, f"coordination namespace {stage.parent}")


def prepare_anchor_publication_stage(
    path: Path,
    content: bytes,
    *,
    kind: str,
    target: Path | None,
) -> AnchorStage:
    if len(content) > METADATA_MAX_BYTES:
        fail(f"coordination anchor {path} exceeds the metadata bound")
    created_parent = ensure_private_directory_component_held(path.parent)
    parent_snapshot = snapshot_directory_metadata(path.parent, f"coordination namespace {path.parent}")
    temporary = anchor_publication_stage_path(path)
    descriptor = open_no_follow(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        OWNER_FILE_MODE,
    )
    descriptor_open = True
    stage_identity: tuple[int, int] | None = identity_of(os.fstat(descriptor))
    try:
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                fail(f"short write while publishing coordination anchor {path}")
            offset += written
        os.fchmod(descriptor, OWNER_FILE_MODE)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor_open = False
        fsync_directory(path.parent)
        validated = validate_anchor_publication_stage(
            temporary,
            path,
            content,
            kind=kind,
            target=target,
        )
    except BaseException:
        if descriptor_open:
            os.close(descriptor)
        if stage_identity is not None:
            unlink_created_stage(temporary, stage_identity, parent_snapshot)
        elif path_exists_no_follow(temporary):
            with contextlib.suppress(FileNotFoundError):
                info = temporary.lstat()
                if stat.S_ISREG(info.st_mode) and owner_of(info) == current_uid():
                    temporary.unlink()
                    fsync_directory(temporary.parent)
            restore_directory_metadata(parent_snapshot, f"coordination namespace {path.parent}")
        rollback_created_directory(created_parent, f"coordination anchor parent {path.parent}")
        raise
    return AnchorStage(
        path=validated.path,
        identity=validated.identity,
        content=validated.content,
        parent_snapshot=parent_snapshot,
        created_parent=created_parent,
    )


def publish_anchor_no_replace(
    path: Path,
    content: bytes,
    *,
    kind: str,
    target: Path | None,
) -> AnchorStage:
    stage = prepare_anchor_publication_stage(path, content, kind=kind, target=target)
    linked = False
    try:
        try:
            os.link(stage.path, path)
            linked = True
        except FileExistsError:
            return
        except OSError as exc:
            try:
                if stage.parent_snapshot is not None:
                    unlink_created_stage(stage.path, stage.identity, stage.parent_snapshot)
                if not path_exists_no_follow(path):
                    rollback_created_directory(
                        stage.created_parent,
                        f"coordination anchor parent {path.parent}",
                    )
            finally:
                close_created_directory_signature(stage.created_parent)
            raise QwenCodeSetupError(f"coordination anchor publication failed: {exc}") from exc
        # The final path is now a monotonic rendezvous object. The publication
        # alias remains until the final inode has been locked and revalidated.
        fsync_directory(path.parent)
    finally:
        if not linked:
            # A complete pre-link stage is durable recovery authority. It is
            # intentionally left for the next exclusive opener to promote or
            # drain after validating the final anchor.
            pass
    return stage


def recover_anchor_publication_alias_after_lock(
    path: Path,
    info: os.stat_result,
    *,
    kind: str,
    target: Path | None,
) -> os.stat_result:
    valid_anchor_publication_stages(
        path,
        anchor_payload(kind, target=target),
        kind=kind,
        target=target,
        final_info=info,
    )
    aliases = publication_aliases_for(path, info)
    if len(aliases) != 1 or info.st_nlink != 2:
        fail(f"coordination anchor {path} has unknown hard-link aliases")
    alias_info = aliases[0].lstat()
    if identity_of(alias_info) != identity_of(info):
        fail_concurrent(f"coordination anchor publication alias changed: {aliases[0]}")
    aliases[0].unlink()
    fsync_directory(path.parent)
    content, current = read_regular_file(
        path,
        f"coordination anchor {path}",
        owner_only=True,
        max_bytes=METADATA_MAX_BYTES,
        allow_hardlinks=True,
    )
    validate_anchor_content(path, content, kind, target)
    if current.st_nlink != 1:
        fail(f"coordination anchor {path} alias recovery did not converge")
    return current


def promote_anchor_stage_before_open(
    path: Path,
    *,
    kind: str,
    target: Path | None,
) -> None:
    if path_exists_no_follow(path):
        return
    content = anchor_payload(kind, target=target)
    stages = valid_anchor_publication_stages(
        path,
        content,
        kind=kind,
        target=target,
    )
    if not stages:
        return
    chosen = stages[0]
    try:
        os.link(chosen.path, path)
    except FileExistsError:
        return
    except FileNotFoundError:
        if path_exists_no_follow(path):
            return
        fail_concurrent(f"coordination anchor publication stage disappeared: {chosen.path}")
    except OSError as exc:
        raise QwenCodeSetupError(f"coordination anchor stage promotion failed: {exc}") from exc
    fsync_directory(path.parent)


def drain_anchor_stages_after_lock(
    path: Path,
    final_info: os.stat_result,
    *,
    kind: str,
    target: Path | None,
) -> os.stat_result:
    content = anchor_payload(kind, target=target)
    stages = valid_anchor_publication_stages(
        path,
        content,
        kind=kind,
        target=target,
        final_info=final_info,
    )
    for stage in stages:
        try:
            info = stage.path.lstat()
        except FileNotFoundError:
            continue
        if identity_of(info) != stage.identity:
            fail_concurrent(f"coordination anchor publication stage changed: {stage.path}")
        stage.path.unlink()
        fsync_directory(stage.path.parent)
    current = validate_anchor_path(
        path,
        kind=kind,
        target=target,
        allow_publication_alias=False,
    )
    return current


@contextlib.contextmanager
def anchor_lock(
    path: Path,
    *,
    kind: str,
    target: Path | None,
    exclusive: bool,
    create: bool,
) -> Iterator[None]:
    published_stage: AnchorStage | None = None
    if create and exclusive:
        if not path_exists_no_follow(path):
            ensure_product_namespace_publishable(path)
        promote_anchor_stage_before_open(path, kind=kind, target=target)
    if create and not path_exists_no_follow(path):
        published_stage = publish_anchor_no_replace(
            path,
            anchor_payload(kind, target=target),
            kind=kind,
            target=target,
        )
    if create and exclusive and not path_exists_no_follow(path):
        promote_anchor_stage_before_open(path, kind=kind, target=target)
    try:
        validate_anchor_path(
            path,
            kind=kind,
            target=target,
            allow_publication_alias=exclusive,
        )
        descriptor = open_no_follow(path, os.O_RDWR)
    except BaseException:
        close_created_directory_signature(
            published_stage.created_parent if published_stage is not None else None
        )
        raise
    try:
        opened = os.fstat(descriptor)
        current = validate_anchor_path(
            path,
            kind=kind,
            target=target,
            allow_publication_alias=exclusive,
        )
        if identity_of(opened) != identity_of(current):
            fail_concurrent(f"coordination anchor {path} changed during open")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        locked = os.fstat(descriptor)
        if identity_of(locked) != identity_of(current):
            fail_concurrent(f"coordination anchor {path} changed during lock")
        if current.st_nlink != 1:
            if not exclusive:
                fail(f"coordination anchor {path} has an incomplete publication alias")
            current = recover_anchor_publication_alias_after_lock(
                path,
                current,
                kind=kind,
                target=target,
            )
            if identity_of(os.fstat(descriptor)) != identity_of(current):
                fail_concurrent(f"coordination anchor {path} changed during alias recovery")
        if exclusive:
            current = drain_anchor_stages_after_lock(
                path,
                current,
                kind=kind,
                target=target,
            )
            if identity_of(os.fstat(descriptor)) != identity_of(current):
                fail_concurrent(f"coordination anchor {path} changed during stage drain")
        close_created_directory_signature(
            published_stage.created_parent if published_stage is not None else None
        )
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
        close_created_directory_signature(
            published_stage.created_parent if published_stage is not None else None
        )


def read_regular_file(
    path: Path,
    label: str,
    *,
    owner_only: bool = False,
    max_bytes: int = MANAGED_PAYLOAD_MAX_BYTES,
    allow_hardlinks: bool = False,
) -> tuple[bytes, os.stat_result]:
    before = require_regular_file(
        path,
        label,
        owner_only=owner_only,
        allow_hardlinks=allow_hardlinks,
    )
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
        if not stat.S_ISREG(opened.st_mode) or (opened.st_nlink != 1 and not allow_hardlinks):
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
    final = require_regular_file(
        path,
        label,
        owner_only=owner_only,
        allow_hardlinks=allow_hardlinks,
    )
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


def validate_profile_id(profile_id: str, *, allow_legacy: bool = False) -> None:
    if not SETUP_ID_PATTERN.fullmatch(profile_id):
        fail(f"invalid profile id: {profile_id!r}")
    allowed = set(PROFILE_IDS)
    if allow_legacy:
        allowed.update(LEGACY_SETUP_PROFILES.values())
    if profile_id not in allowed:
        fail(f"unsupported profile id: {profile_id}")


def normalized_content_setup_id(raw_setup_id: str) -> str:
    validate_setup_id(raw_setup_id)
    if raw_setup_id == CONTENT_SETUP_ID:
        return CONTENT_SETUP_ID
    if raw_setup_id in LEGACY_SETUP_PROFILES:
        return CONTENT_SETUP_ID
    fail(f"unsupported managed stamp setup_id: {raw_setup_id}")


def legacy_profile_for_setup(raw_setup_id: str) -> str | None:
    profile = LEGACY_SETUP_PROFILES.get(raw_setup_id)
    return None if profile == "balanced" else profile


def resolve_setup_profile(setup_id: str | None, profile_id: str | None) -> tuple[str, str]:
    selected_setup = setup_id or CONTENT_SETUP_ID
    validate_setup_id(selected_setup)
    if selected_setup != CONTENT_SETUP_ID:
        if selected_setup in {"safe", "full-auto"} and profile_id is None:
            return CONTENT_SETUP_ID, selected_setup
        if selected_setup in {"safe", "full-auto"}:
            fail("legacy setup ids cannot be combined with --profile")
        if selected_setup == "balanced":
            fail(
                "legacy setup id 'balanced' requires migration to "
                "--setup nddev-builder with --profile full-auto or --profile safe"
            )
        fail(f"unsupported setup id: {selected_setup}")
    selected_profile = profile_id or DEFAULT_PROFILE_ID
    validate_profile_id(selected_profile)
    return selected_setup, selected_profile


def ensure_lf_text(content: bytes, label: str) -> None:
    try:
        content.decode("utf-8")
    except UnicodeDecodeError as exc:
        fail(f"{label} must be valid UTF-8: {exc}")
    if not content or not content.endswith(b"\n") or b"\r" in content:
        fail(f"{label} must be non-empty LF-terminated text")


def validate_setup_settings(setup_id: str, settings: dict[str, Any]) -> None:
    if setup_id != CONTENT_SETUP_ID:
        fail(f"unsupported setup id: {setup_id}")
    tools = settings.get("tools")
    privacy = settings.get("privacy")
    context = settings.get("context")
    if not isinstance(tools, dict):
        fail(f"setup {setup_id}/settings.json tools must be an object")
    if "approvalMode" in tools or "sandbox" in tools:
        fail(f"setup {setup_id}/settings.json must not own profile approval settings")
    if not isinstance(privacy, dict) or privacy.get("usageStatisticsEnabled") is not False:
        fail(f"setup {setup_id}/settings.json must disable usage statistics")
    if not isinstance(context, dict) or context.get("fileName") != ["QWEN.md"]:
        fail(f"setup {setup_id}/settings.json must select QWEN.md context")


def load_profile(profile_id: str) -> dict[str, Any]:
    validate_profile_id(profile_id)
    profile_path = PROFILE_ROOT / profile_id / "profile.json"
    profile = load_json_object(profile_path, f"profile {profile_id}")
    require_exact_keys(
        profile,
        {"schema_version", "id", "description", "settings_overlay"},
        f"profile {profile_id}",
    )
    if profile["schema_version"] != 1 or profile["id"] != profile_id:
        fail(f"profile {profile_id} identity or schema mismatch")
    if not isinstance(profile["description"], str) or not profile["description"].strip():
        fail(f"profile {profile_id} description must be non-empty")
    overlay = profile["settings_overlay"]
    if not isinstance(overlay, dict):
        fail(f"profile {profile_id} settings_overlay must be an object")
    tools = overlay.get("tools")
    if not isinstance(tools, dict):
        fail(f"profile {profile_id} settings_overlay.tools must be an object")
    approval, sandbox = PROFILE_TOOL_POLICY[profile_id]
    if tools.get("approvalMode") != approval or tools.get("sandbox") is not sandbox:
        fail(f"profile {profile_id} approval or sandbox policy mismatch")
    return profile


def merge_json_objects(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(base, sort_keys=True))
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge_json_objects(result[key], value)
        else:
            result[key] = json.loads(json.dumps(value, sort_keys=True))
    return result


def apply_profile_to_settings(settings: dict[str, Any], profile_id: str) -> dict[str, Any]:
    if profile_id in PROFILE_IDS:
        profile = load_profile(profile_id)
        return merge_json_objects(settings, profile["settings_overlay"])
    validate_profile_id(profile_id, allow_legacy=True)
    approval, sandbox = LEGACY_PROFILE_TOOL_POLICY[profile_id]
    return merge_json_objects(settings, {"tools": {"approvalMode": approval, "sandbox": sandbox}})


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


def render_setup(
    setup_id: str = CONTENT_SETUP_ID, profile_id: str = DEFAULT_PROFILE_ID
) -> tuple[dict[str, Any], dict[str, bytes]]:
    validate_setup_id(setup_id)
    if setup_id != CONTENT_SETUP_ID:
        fail(f"unsupported setup id: {setup_id}")
    validate_profile_id(profile_id)
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
            "default_profile",
            "profiles",
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
    if metadata["default_profile"] != DEFAULT_PROFILE_ID:
        fail(f"setup {setup_id} default profile declaration is invalid")
    if metadata["profiles"] != list(PROFILE_IDS):
        fail(f"setup {setup_id} profile declaration is invalid")
    if not isinstance(metadata["description"], str) or not metadata["description"].strip():
        fail(f"setup {setup_id} description must be non-empty")

    settings_content, _ = read_regular_file(
        setup_root / "settings.json",
        f"setup {setup_id}/settings.json",
        max_bytes=METADATA_MAX_BYTES,
    )
    settings = parse_json_object(settings_content, f"setup {setup_id}/settings.json")
    validate_setup_settings(setup_id, settings)
    settings = apply_profile_to_settings(settings, profile_id)
    rendered: dict[str, bytes] = {"settings.json": canonical_json(settings)}
    for name in MANAGED_FILES:
        if name == "settings.json":
            continue
        content, _ = read_regular_file(setup_root / name, f"setup {setup_id}/{name}")
        ensure_lf_text(content, f"setup {setup_id}/{name}")
        rendered[name] = content
    return metadata, {**rendered, **render_builder()}


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
                "default_profile": metadata["default_profile"],
                "profiles": metadata["profiles"],
            }
        )
    if not entries:
        fail("setup catalog is empty")
    return entries


def list_profiles() -> list[dict[str, Any]]:
    if not PROFILE_ROOT.is_dir() or PROFILE_ROOT.is_symlink():
        fail("profile catalog is missing or unsafe")
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in sorted(PROFILE_ROOT.iterdir(), key=lambda path: path.name):
        if not candidate.is_dir() or candidate.is_symlink():
            fail(f"profile catalog entry must be a real directory: {candidate.name}")
        profile = load_profile(candidate.name)
        seen.add(profile["id"])
        entries.append(
            {
                "id": profile["id"],
                "description": profile["description"],
                "default": profile["id"] == DEFAULT_PROFILE_ID,
            }
        )
    if tuple(sorted(seen)) != tuple(sorted(PROFILE_IDS)):
        fail("profile catalog identity mismatch")
    return entries


def validate_lexical_target(raw_target: str) -> Path:
    expanded = Path(raw_target).expanduser()
    if not expanded.is_absolute():
        fail("--target must be an absolute path")
    target = Path(os.path.normpath(str(expanded)))
    if target == Path(target.anchor):
        fail("filesystem root cannot be a target")
    return target


def canonicalize_target(lexical_target: Path) -> Path:
    try:
        raw_info = lexical_target.lstat()
    except FileNotFoundError:
        raw_info = None
    if raw_info is not None and stat.S_ISLNK(raw_info.st_mode):
        fail("--target must not be a symlink")
    raw_parent = lexical_target.parent
    try:
        parent = raw_parent.resolve(strict=True)
    except FileNotFoundError:
        fail("--target parent must already exist")
    target = parent / lexical_target.name
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


def resolve_target(raw_target: str) -> Path:
    return canonicalize_target(validate_lexical_target(raw_target))


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


def create_or_require_private_child_directory(root: Path, relative: Path) -> Path:
    if relative.is_absolute() or ".." in relative.parts:
        fail(f"private directory path escapes target: {relative}")
    cursor = root
    require_directory(cursor, "private directory root")
    for part in relative.parts:
        cursor = cursor / part
        try:
            info = cursor.lstat()
        except FileNotFoundError:
            cursor.mkdir(mode=OWNER_DIRECTORY_MODE)
            cursor.chmod(OWNER_DIRECTORY_MODE)
            fsync_directory(cursor.parent)
            info = cursor.lstat()
        if not is_owner_private_directory(info):
            fail(f"private directory must be a real owner-only directory: {cursor}")
    try:
        cursor.resolve(strict=True).relative_to(root.resolve(strict=True))
    except ValueError:
        fail(f"private directory escaped target: {cursor}")
    return cursor


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


def validate_digest_map(
    value: Any, label: str, expected_paths: tuple[str, ...] = CURRENT_PAYLOAD_PATHS
) -> dict[str, str | None]:
    if not isinstance(value, dict) or set(value) != set(expected_paths):
        fail(f"{label} must declare exactly managed payload paths")
    result: dict[str, str | None] = {}
    for name in expected_paths:
        digest = value[name]
        if digest is not None and (
            not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None
        ):
            fail(f"{label}.{name} must be null or a lowercase SHA-256 digest")
        result[name] = digest
    return result


def backup_record_paths(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, dict):
        fail(f"{label} must declare exactly managed payload paths")
    actual = set(value)
    if actual == set(ALL_PAYLOAD_PATHS):
        return ALL_PAYLOAD_PATHS
    if actual == set(LEGACY_SCHEMA1_PAYLOAD_PATHS):
        return LEGACY_SCHEMA1_PAYLOAD_PATHS
    if actual == set(CURRENT_PAYLOAD_PATHS):
        return CURRENT_PAYLOAD_PATHS
    fail(f"{label} must declare exactly managed payload paths")


def validate_backup_record_map(value: Any, label: str) -> dict[str, dict[str, Any] | None]:
    expected_paths = backup_record_paths(value, label)
    if not isinstance(value, dict):
        fail(f"{label} must declare exactly managed payload paths")
    result: dict[str, dict[str, Any] | None] = {}
    for name in expected_paths:
        record = value[name]
        if record is None:
            result[name] = None
            continue
        if not isinstance(record, dict):
            fail(f"{label}.{name} must be null or an exact payload record")
        require_exact_keys(record, BACKUP_RECORD_KEYS, f"{label}.{name}")
        if record["path"] != name:
            fail(f"{label}.{name}.path mismatch")
        if not isinstance(record["size"], int) or record["size"] < 0:
            fail(f"{label}.{name}.size must be a non-negative integer")
        digest = record["sha256"]
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            fail(f"{label}.{name}.sha256 must be a lowercase SHA-256 digest")
        result[name] = record
    return result


def stamp_bytes(target: Path, setup_id: str, profile_id: str, rendered: dict[str, bytes]) -> bytes:
    setup_id, profile_id = resolve_setup_profile(setup_id, profile_id)
    return canonical_json(
        {
            "schema_version": 2,
            "product_name": PRODUCT_NAME,
            "build_version": VERSION,
            "setup_id": setup_id,
            "profile_id": profile_id,
            "canonical_target": str(target),
            "managed_paths": {name: sha256_bytes(rendered[name]) for name in CURRENT_PAYLOAD_PATHS},
            "builder_projection": BUILDER_PROJECTION,
        }
    )


def validate_common_stamp_fields(value: dict[str, Any], target: Path) -> None:
    if value["product_name"] != PRODUCT_NAME:
        fail("managed stamp product identity is invalid")
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


def normalize_stamp(value: dict[str, Any], target: Path) -> dict[str, Any]:
    schema_version = value.get("schema_version")
    if schema_version == 1:
        require_exact_keys(value, STAMP_V1_KEYS, "managed stamp")
        validate_common_stamp_fields(value, target)
        raw_setup_id = value["setup_id"]
        if raw_setup_id not in LEGACY_SETUP_PROFILES:
            fail(f"unsupported schema-1 managed stamp setup_id: {raw_setup_id}")
        validate_digest_map(
            value["managed_paths"],
            "managed stamp managed_paths",
            LEGACY_SCHEMA1_PAYLOAD_PATHS,
        )
        normalized = dict(value)
        normalized["_content_setup_id"] = CONTENT_SETUP_ID
        normalized["_profile_id"] = legacy_profile_for_setup(raw_setup_id)
        normalized["_legacy_setup_id"] = raw_setup_id
        normalized["_migration_required"] = raw_setup_id == "balanced"
        normalized["_payload_paths"] = LEGACY_SCHEMA1_PAYLOAD_PATHS
        return normalized
    if schema_version == 2:
        require_exact_keys(value, STAMP_V2_KEYS, "managed stamp")
        validate_common_stamp_fields(value, target)
        if value["setup_id"] != CONTENT_SETUP_ID:
            fail("schema-2 managed stamp setup_id must be nddev-builder")
        if not isinstance(value["profile_id"], str):
            fail("schema-2 managed stamp profile_id must be a string")
        validate_profile_id(value["profile_id"])
        if value["builder_projection"] != BUILDER_PROJECTION:
            fail("schema-2 managed stamp builder projection mismatch")
        validate_digest_map(value["managed_paths"], "managed stamp managed_paths")
        normalized = dict(value)
        normalized["_content_setup_id"] = CONTENT_SETUP_ID
        normalized["_profile_id"] = value["profile_id"]
        normalized["_legacy_setup_id"] = None
        normalized["_migration_required"] = False
        normalized["_payload_paths"] = CURRENT_PAYLOAD_PATHS
        return normalized
    fail("managed stamp schema_version is invalid")


def load_stamp_from_bytes(content: bytes, target: Path) -> dict[str, Any]:
    value = parse_json_object(content, f"managed stamp {target / STAMP_NAME}")
    return normalize_stamp(value, target)


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
    return load_stamp_from_bytes(content, target)


def profile_from_settings(current: bytes) -> str | None:
    try:
        current_settings = json.loads(current.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(current_settings, dict):
        return None
    tools = current_settings.get("tools")
    if not isinstance(tools, dict):
        return None
    for profile_id, (approval, sandbox) in LEGACY_PROFILE_TOOL_POLICY.items():
        if tools.get("approvalMode") == approval and tools.get("sandbox") is sandbox:
            return profile_id
    return None


def profile_for_stamp(target: Path, stamp: dict[str, Any]) -> str | None:
    del target
    return stamp["_profile_id"]


def rendered_settings_for_profile(profile_id: str) -> bytes:
    setup_root = CATALOG_ROOT / CONTENT_SETUP_ID
    settings_content, _ = read_regular_file(
        setup_root / "settings.json",
        f"setup {CONTENT_SETUP_ID}/settings.json",
        max_bytes=METADATA_MAX_BYTES,
    )
    settings = parse_json_object(settings_content, f"setup {CONTENT_SETUP_ID}/settings.json")
    validate_setup_settings(CONTENT_SETUP_ID, settings)
    return canonical_json(apply_profile_to_settings(settings, profile_id))


def settings_overlay(current: bytes, profile_id: str) -> dict[str, Any]:
    try:
        current_settings = json.loads(current.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(current_settings, dict):
        return {}
    base_settings = parse_json_object(
        rendered_settings_for_profile(profile_id),
        f"profile {profile_id} rendered settings",
    )
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


def settings_managed_intact(current: bytes, profile_id: str | None) -> bool:
    if profile_id is None:
        return False
    try:
        current_settings = json.loads(current.decode("utf-8"))
        base = parse_json_object(
            rendered_settings_for_profile(profile_id),
            f"profile {profile_id} rendered settings",
        )
    except (UnicodeDecodeError, json.JSONDecodeError, QwenCodeSetupError):
        return False
    if not isinstance(current_settings, dict):
        return False
    for key in SETTINGS_SETUP_KEYS:
        if current_settings.get(key) != base.get(key):
            return False
    return True


def inspect_target(target: Path) -> dict[str, Any]:
    root = require_control_root(create=False)
    cleanup_pending = cleanup_pending_for_target(root, target, read_only=True)
    if not ensure_private_directory(target, create=False):
        return {
            "state": "missing",
            "setup_id": None,
            "profile_id": None,
            "legacy_setup_id": None,
            "raw_setup_id": None,
            "stamp_schema_version": None,
            "migration_required": False,
            "build_version": None,
            "drift": [],
            "unmanaged_managed_paths": [],
            "builder_extension": "missing",
            "cleanup_pending": cleanup_pending,
        }
    stamp = load_stamp(target)
    existing: list[str] = []
    for name in ALL_PAYLOAD_PATHS:
        path = target_path(target, name)
        if path_exists_no_follow(path):
            require_regular_file(path, f"managed path {path}")
            existing.append(name)
    if stamp is None:
        return {
            "state": "unmanaged",
            "setup_id": None,
            "profile_id": None,
            "legacy_setup_id": None,
            "raw_setup_id": None,
            "stamp_schema_version": None,
            "migration_required": False,
            "build_version": None,
            "drift": [],
            "unmanaged_managed_paths": existing,
            "builder_extension": "present"
            if (target / "extensions" / "nddev-builder").exists()
            else "missing",
            "cleanup_pending": cleanup_pending,
        }
    expected = validate_digest_map(
        stamp["managed_paths"],
        "managed stamp managed_paths",
        stamp["_payload_paths"],
    )
    drift: list[str] = []
    for name in stamp["_payload_paths"]:
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
            profile_for_stamp(target, stamp),
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
        "setup_id": stamp["_content_setup_id"],
        "profile_id": profile_for_stamp(target, stamp),
        "legacy_setup_id": stamp["_legacy_setup_id"],
        "raw_setup_id": stamp["setup_id"],
        "stamp_schema_version": stamp["schema_version"],
        "migration_required": stamp["_migration_required"],
        "build_version": stamp["build_version"],
        "drift": drift,
        "unmanaged_managed_paths": [],
        "builder_extension": "present"
        if not any(name in drift for name in BUILDER_FILES)
        else "drift",
        "cleanup_pending": cleanup_pending,
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
    del target
    yield


def cleanup_namespace_paths(root: Path, target: Path) -> dict[str, Path]:
    cleanup_root = cleanup_root_path(root, target)
    return {
        "root": cleanup_root,
        "prepare": cleanup_root / CLEANUP_PREPARE_NAME,
        "pending": cleanup_root / CLEANUP_JOURNAL_NAME,
        "tombstones": cleanup_root / CLEANUP_TOMBSTONE_DIRECTORY,
    }


def ensure_cleanup_namespace(root: Path, target: Path) -> dict[str, Path]:
    paths = cleanup_namespace_paths(root, target)
    ensure_private_directory_component(root / CLEANUP_DIRECTORY_NAME)
    ensure_private_directory_component(paths["root"])
    ensure_private_directory_component(paths["tombstones"])
    return paths


def validate_relative_name(name: str, label: str) -> Path:
    if not isinstance(name, str) or not name:
        fail(f"{label} must be a non-empty relative path")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
        fail(f"{label} must be bounded and relative")
    return relative


def cleanup_tree_record(path: Path, base: Path, counter: dict[str, int]) -> dict[str, Any]:
    counter["entries"] += 1
    if counter["entries"] > CLEANUP_MAX_TREE_ENTRIES:
        fail(f"cleanup tree exceeds {CLEANUP_MAX_TREE_ENTRIES} entries")
    info = path.lstat()
    relative = "." if path == base else str(path.relative_to(base))
    common: dict[str, Any] = {
        "path": relative,
        "mode": mode_of(info),
        "uid": owner_of(info),
        "nlink": info.st_nlink,
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }
    if stat.S_ISLNK(info.st_mode):
        fail("cleanup tombstone must not contain symlinks")
    if stat.S_ISREG(info.st_mode):
        if info.st_nlink != 1:
            fail("cleanup source file must not have hard-link aliases")
        content, reopened = read_regular_file(path, f"cleanup source {relative}", owner_only=False)
        if identity_of(reopened) != identity_of(info):
            fail_concurrent(f"cleanup source changed during snapshot: {path}")
        common.update({"type": "file", "sha256": sha256_bytes(content)})
        return common
    if stat.S_ISDIR(info.st_mode):
        if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
            fail("cleanup directory must be owned by the current user")
        children = sorted(child.name for child in path.iterdir())
        common.update({"type": "directory", "children": children})
        return common
    fail("cleanup source has unsupported file type")


def snapshot_cleanup_tree(path: Path) -> list[dict[str, Any]]:
    if not path_exists_no_follow(path):
        fail(f"cleanup source is missing: {path}")
    records: list[dict[str, Any]] = []
    counter = {"entries": 0}
    records.append(cleanup_tree_record(path, path, counter))
    if path.is_dir() and not path.is_symlink():
        for child in sorted(path.rglob("*"), key=lambda item: str(item.relative_to(path))):
            records.append(cleanup_tree_record(child, path, counter))
    encoded = canonical_json(records)
    if len(encoded) > CLEANUP_MAX_SERIALIZED_BYTES:
        fail(f"cleanup journal exceeds {CLEANUP_MAX_SERIALIZED_BYTES} serialized bytes")
    return records


def validate_cleanup_document(value: dict[str, Any], target: Path, label: str) -> None:
    require_exact_keys(
        value,
        {
            "schema_version",
            "product_name",
            "canonical_target",
            "target_digest",
            "entries",
        },
        label,
    )
    if (
        value["schema_version"] != 1
        or value["product_name"] != PRODUCT_NAME
        or value["canonical_target"] != str(target)
        or value["target_digest"] != target_digest(target)
    ):
        fail(f"{label} identity or target binding is invalid")
    entries = value["entries"]
    if not isinstance(entries, list) or len(entries) > CLEANUP_MAX_TREE_ENTRIES:
        fail(f"{label} entries are invalid")
    seen_names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            fail(f"{label} entries must be objects")
        require_exact_keys(entry, {"name", "reason", "records"}, f"{label} entry")
        name = entry["name"]
        validate_relative_name(name, f"{label} entry name")
        if name in seen_names:
            fail(f"{label} contains duplicate entry {name}")
        seen_names.add(name)
        if not isinstance(entry["reason"], str) or not entry["reason"]:
            fail(f"{label} entry reason must be non-empty")
        records = entry["records"]
        if not isinstance(records, list) or not records:
            fail(f"{label} entry records must be non-empty")
        record_paths = [record.get("path") for record in records if isinstance(record, dict)]
        if record_paths[0:1] != ["."] or len(record_paths) != len(records):
            fail(f"{label} entry records are malformed")
        if len(set(record_paths)) != len(record_paths):
            fail(f"{label} entry records contain duplicates")
        for record in records:
            validate_cleanup_record_schema(record, f"{label} entry record")


def validate_cleanup_record_schema(record: Any, label: str) -> None:
    if not isinstance(record, dict):
        fail(f"{label} must be an object")
    common = {"path", "mode", "uid", "nlink", "dev", "ino", "size", "mtime_ns", "type"}
    record_type = record.get("type")
    if record_type == "file":
        require_exact_keys(record, common | {"sha256"}, label)
        digest = record["sha256"]
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            fail(f"{label}.sha256 must be a lowercase SHA-256 digest")
    elif record_type == "directory":
        require_exact_keys(record, common | {"children"}, label)
        children = record["children"]
        if not isinstance(children, list) or any(not isinstance(item, str) for item in children):
            fail(f"{label}.children must be a string list")
        if children != sorted(children) or len(children) != len(set(children)):
            fail(f"{label}.children must be sorted and unique")
    else:
        fail(f"{label}.type is invalid")
    path = record["path"]
    if path != ".":
        validate_relative_name(path, f"{label}.path")
    for key in ("mode", "uid", "nlink", "dev", "ino", "size", "mtime_ns"):
        if not isinstance(record[key], int) or record[key] < 0:
            fail(f"{label}.{key} must be a non-negative integer")


def cleanup_record_by_path(entry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = entry["records"]
    return {record["path"]: record for record in records}


def current_cleanup_paths(root: Path) -> set[str]:
    if not path_exists_no_follow(root):
        return set()
    result = {"."}
    if root.is_dir() and not root.is_symlink():
        for path in root.rglob("*"):
            result.add(str(path.relative_to(root)))
    return result


def validate_cleanup_object(
    path: Path, record: dict[str, Any], *, deleting_directory: bool
) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode):
        fail(f"cleanup tombstone object is a symlink: {record['path']}")
    if identity_of(info) != (record["dev"], record["ino"]):
        fail(f"cleanup tombstone object identity changed: {record['path']}")
    if owner_of(info) != record["uid"] or stat.S_IMODE(info.st_mode) != record["mode"]:
        fail(f"cleanup tombstone object mode or owner changed: {record['path']}")
    if record["type"] == "file":
        if not stat.S_ISREG(info.st_mode):
            fail(f"cleanup tombstone file changed kind: {record['path']}")
        if info.st_nlink != record["nlink"] or info.st_size != record["size"]:
            fail(f"cleanup tombstone file metadata changed: {record['path']}")
        if info.st_mtime_ns != record["mtime_ns"]:
            fail(f"cleanup tombstone file mtime changed: {record['path']}")
        content, reopened = read_regular_file(path, f"cleanup tombstone file {record['path']}")
        if identity_of(reopened) != identity_of(info):
            fail_concurrent(f"cleanup tombstone file changed while being read: {record['path']}")
        if sha256_bytes(content) != record["sha256"]:
            fail(f"cleanup tombstone file digest mismatch: {record['path']}")
        return
    if record["type"] != "directory" or not stat.S_ISDIR(info.st_mode):
        fail(f"cleanup tombstone directory changed kind: {record['path']}")
    if not deleting_directory and (
        info.st_nlink != record["nlink"]
        or info.st_size != record["size"]
        or info.st_mtime_ns != record["mtime_ns"]
    ):
        fail(f"cleanup tombstone directory metadata changed: {record['path']}")


def drain_tombstone_entry(tombstone: Path, entry: dict[str, Any]) -> None:
    records = cleanup_record_by_path(entry)
    declared = set(records)
    present = current_cleanup_paths(tombstone)
    unknown = sorted(present - declared)
    if unknown:
        fail(f"cleanup tombstone contains unknown entries: {', '.join(unknown)}")

    def cleanup_depth(value: str) -> int:
        return 0 if value == "." else len(Path(value).parts)

    for relative in sorted(declared, key=cleanup_depth, reverse=True):
        record = records[relative]
        path = tombstone if relative == "." else tombstone / relative
        if not path_exists_no_follow(path):
            continue
        if record["type"] == "directory":
            current_children = sorted(child.name for child in path.iterdir())
            declared_children: set[str] = set()
            directory_relative = Path(relative)
            for name in declared:
                if name == ".":
                    continue
                candidate = Path(name)
                if relative == ".":
                    if len(candidate.parts) == 1:
                        declared_children.add(candidate.parts[0])
                    continue
                try:
                    remainder = candidate.relative_to(directory_relative)
                except ValueError:
                    continue
                if len(remainder.parts) == 1:
                    declared_children.add(remainder.parts[0])
            if any(child not in declared_children for child in current_children):
                fail(f"cleanup tombstone directory contains unknown children: {relative}")
            validate_cleanup_object(path, record, deleting_directory=True)
            if current_children:
                continue
            path.rmdir()
            fsync_directory(path.parent)
            continue
        validate_cleanup_object(path, record, deleting_directory=False)
        path.unlink()
        fsync_directory(path.parent)
    if path_exists_no_follow(tombstone):
        fail(f"cleanup tombstone did not drain completely: {entry['name']}")


def recover_cleanup_publication_alias(path: Path, info: os.stat_result) -> None:
    aliases = publication_aliases_for(path, info)
    if len(aliases) != 1 or info.st_nlink != 2:
        fail(f"cleanup journal has unknown hard-link aliases: {path}")
    aliases[0].unlink()
    fsync_directory(path.parent)


def load_cleanup_document(
    path: Path,
    target: Path,
    label: str,
    *,
    recover_alias: bool = False,
) -> dict[str, Any]:
    content, info = read_regular_file(
        path,
        label,
        owner_only=True,
        max_bytes=CLEANUP_MAX_SERIALIZED_BYTES,
        allow_hardlinks=True,
    )
    if info.st_nlink != 1:
        if not recover_alias:
            fail(f"{label} has incomplete no-replace publication")
        recover_cleanup_publication_alias(path, info)
        content, info = read_regular_file(
            path,
            label,
            owner_only=True,
            max_bytes=CLEANUP_MAX_SERIALIZED_BYTES,
        )
        if info.st_nlink != 1:
            fail(f"{label} alias recovery did not converge")
    value = parse_json_object(content, label)
    validate_cleanup_document(value, target, label)
    return value


def cleanup_pending_for_target(root: Path | None, target: Path, *, read_only: bool) -> bool:
    if root is None:
        return False
    paths = cleanup_namespace_paths(root, target)
    cleanup_root = paths["root"]
    if not path_exists_no_follow(cleanup_root):
        return False
    info = require_directory(cleanup_root, "cleanup namespace")
    if not is_owner_private_directory(info):
        fail("cleanup namespace must be private and manager-owned")
    allowed = {CLEANUP_PREPARE_NAME, CLEANUP_JOURNAL_NAME, CLEANUP_TOMBSTONE_DIRECTORY}
    entries = {entry.name for entry in cleanup_root.iterdir()}
    publication_alias_names = {
        name
        for name in entries
        if (
            name.startswith(f"{ANCHOR_PUBLICATION_PREFIX}{CLEANUP_JOURNAL_NAME}.")
            or name.startswith(f"{ANCHOR_PUBLICATION_PREFIX}{CLEANUP_PREPARE_NAME}.")
        )
        and name.endswith(".tmp")
    }
    if read_only and publication_alias_names:
        fail(
            "cleanup namespace contains unknown entries: "
            + ", ".join(sorted(publication_alias_names))
        )
    unknown = sorted(entries - allowed - publication_alias_names)
    if unknown:
        fail(f"cleanup namespace contains unknown entries: {', '.join(unknown)}")
    prepare_exists = path_exists_no_follow(paths["prepare"])
    pending_exists = path_exists_no_follow(paths["pending"])
    if prepare_exists and pending_exists:
        prepare = load_cleanup_document(
            paths["prepare"],
            target,
            "cleanup prepare intent",
            recover_alias=not read_only,
        )
        journal = load_cleanup_document(
            paths["pending"],
            target,
            "cleanup journal",
            recover_alias=not read_only,
        )
        if canonical_json(prepare) != canonical_json(journal):
            fail("cleanup prepare intent does not match committed cleanup journal")
        return True
    if prepare_exists:
        if read_only:
            fail("cleanup prepare intent is pending")
        return True
    if not pending_exists:
        return False
    load_cleanup_document(
        paths["pending"],
        target,
        "cleanup journal",
        recover_alias=not read_only,
    )
    return True


def unlink_tree_bottom_up(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
        fsync_directory(path.parent)
        return
    if not path.is_dir():
        fail(f"cleanup tombstone has unsupported type: {path}")
    for child in sorted(path.iterdir(), key=lambda item: len(item.parts), reverse=True):
        unlink_tree_bottom_up(child)
    path.rmdir()
    fsync_directory(path.parent)


def drain_cleanup(root: Path, target: Path, *, read_only: bool) -> bool:
    pending = cleanup_pending_for_target(root, target, read_only=read_only)
    if not pending or read_only:
        return pending
    paths = cleanup_namespace_paths(root, target)
    if path_exists_no_follow(paths["prepare"]) and not path_exists_no_follow(paths["pending"]):
        prepare = load_cleanup_document(
            paths["prepare"],
            target,
            "cleanup prepare intent",
            recover_alias=True,
        )
        journal_content = canonical_json(prepare)
        publish_no_replace_file(
            paths["pending"],
            journal_content,
            max_bytes=CLEANUP_MAX_SERIALIZED_BYTES,
        )
        fsync_directory(paths["root"])
    journal = load_cleanup_document(
        paths["pending"],
        target,
        "cleanup journal",
        recover_alias=True,
    )
    tombstone_root = paths["tombstones"]
    require_directory(tombstone_root, "cleanup tombstone parent")
    declared = {entry["name"] for entry in journal["entries"]}
    present = {entry.name for entry in tombstone_root.iterdir()}
    unknown = sorted(present - declared)
    if unknown:
        fail(f"cleanup tombstone parent contains unknown entries: {', '.join(unknown)}")
    for entry in journal["entries"]:
        tombstone = tombstone_root / entry["name"]
        if path_exists_no_follow(tombstone):
            drain_tombstone_entry(tombstone, entry)
    if any(tombstone_root.iterdir()):
        fail("cleanup tombstone parent did not drain completely")
    with contextlib.suppress(FileNotFoundError):
        paths["prepare"].unlink()
        fsync_directory(paths["root"])
    with contextlib.suppress(FileNotFoundError):
        paths["pending"].unlink()
        fsync_directory(paths["root"])
    for directory in (tombstone_root, paths["root"], paths["root"].parent):
        try:
            directory.rmdir()
            fsync_directory(directory.parent)
        except OSError as exc:
            if exc.errno not in {errno.ENOENT, errno.ENOTEMPTY}:
                raise
    return False


def publish_cleanup_document(path: Path, value: dict[str, Any]) -> None:
    if path_exists_no_follow(path):
        fail(f"cleanup document already exists: {path}")
    content = canonical_json(value)
    if len(content) > CLEANUP_MAX_SERIALIZED_BYTES:
        fail(f"cleanup document exceeds {CLEANUP_MAX_SERIALIZED_BYTES} serialized bytes")
    publish_no_replace_file(path, content, max_bytes=CLEANUP_MAX_SERIALIZED_BYTES)


def retire_path_after_commit(root: Path, target: Path, path: Path, reason: str) -> bool:
    if not path_exists_no_follow(path):
        return False
    paths = ensure_cleanup_namespace(root, target)
    tombstone_name = f"{reason}-{os.getpid()}-{time.time_ns()}-{uuid.uuid4().hex}"
    validate_relative_name(tombstone_name, "cleanup tombstone name")
    tombstone = paths["tombstones"] / tombstone_name
    records = snapshot_cleanup_tree(path)
    document = {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "canonical_target": str(target),
        "target_digest": target_digest(target),
        "entries": [{"name": tombstone_name, "reason": reason, "records": records}],
    }
    publish_cleanup_document(paths["prepare"], document)
    try:
        path.rename(tombstone)
        fsync_directory(path.parent)
        fsync_directory(tombstone.parent)
        publish_cleanup_document(paths["pending"], document)
        fsync_directory(paths["root"])
        with contextlib.suppress(FileNotFoundError):
            paths["prepare"].unlink()
            fsync_directory(paths["root"])
    except BaseException:
        if path_exists_no_follow(paths["pending"]) or (
            path_exists_no_follow(paths["prepare"]) and path_exists_no_follow(tombstone)
        ):
            return True
        if path_exists_no_follow(tombstone) and not path_exists_no_follow(path):
            with contextlib.suppress(OSError):
                tombstone.rename(path)
                fsync_directory(path.parent)
                fsync_directory(tombstone.parent)
        raise
    try:
        drain_cleanup(root, target, read_only=False)
    except BaseException:
        return cleanup_pending_for_target(root, target, read_only=False)
    return cleanup_pending_for_target(root, target, read_only=False)


def atomic_write(path: Path, content: bytes) -> None:
    durable_write_file(path, content, OWNER_FILE_MODE)


def durable_write_file(path: Path, content: bytes, mode: int) -> None:
    mkdirs_for_file(path)
    if path.exists() or path.is_symlink():
        require_regular_file(path, f"managed path {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    temp_path = Path(temporary)
    try:
        try:
            offset = 0
            while offset < len(content):
                written = os.write(fd, content[offset:])
                if written <= 0:
                    fail(f"short write while writing {path}")
                offset += written
            os.fchmod(fd, mode)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_path, path)
        path.chmod(mode)
        fsync_directory(path.parent)
    finally:
        if temp_path.exists():
            temp_path.unlink()
            fsync_directory(temp_path.parent)


def prune_empty_managed_dirs(target: Path) -> None:
    for relative in (
        "extensions/nddev-builder/skills/qwen-builder-orientation",
        "extensions/nddev-builder/skills",
        "extensions/nddev-builder/agents",
        "extensions/nddev-builder",
        "extensions",
        ".claude",
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


def move_managed_path(source: Path, destination: Path) -> None:
    mkdirs_for_file(destination)
    os.replace(source, destination)
    fsync_directory(source.parent)
    fsync_directory(destination.parent)


def restore_managed_hold(
    target: Path,
    hold: Path,
    moved: list[str],
    desired: dict[str, bytes | None],
    changing: list[str],
) -> None:
    for name in reversed(changing):
        path = target_path(target, name)
        if desired.get(name) is not None and path_exists_no_follow(path):
            path.unlink()
            fsync_directory(path.parent)
    for name in reversed(moved):
        saved = hold / name
        if path_exists_no_follow(saved):
            move_managed_path(saved, target_path(target, name))
    if path_exists_no_follow(hold):
        cleanup_path(hold)
        fsync_directory(hold.parent)


def replace_managed_state(
    target: Path,
    desired: dict[str, bytes | None],
    expected: dict[str, FileSnapshot | None],
    *,
    root: Path,
    postcondition: Any | None = None,
) -> bool:
    assert_snapshot(target, expected)
    hold = (
        target.parent
        / f".{target.name}.nddev-qwen-code-managed-hold.{os.getpid()}.{uuid.uuid4().hex}"
    )
    moved: list[str] = []
    changing: list[str] = []
    try:
        hold.mkdir(mode=OWNER_DIRECTORY_MODE)
        hold.chmod(OWNER_DIRECTORY_MODE)
        fsync_directory(hold.parent)
        for name in MANAGED_PATHS:
            path = target_path(target, name)
            content = desired.get(name)
            snapshot = expected[name]
            wanted_digest = sha256_bytes(content) if content is not None else None
            current_digest = snapshot.digest if snapshot is not None else None
            if current_digest == wanted_digest:
                continue
            changing.append(name)
            if path_exists_no_follow(path):
                if snapshot_file(path, owner_only=False) != snapshot:
                    fail_concurrent(f"managed path changed concurrently: {path}")
                saved = hold / name
                move_managed_path(path, saved)
                moved.append(name)
        for name in changing:
            content = desired.get(name)
            if content is None:
                continue
            atomic_write(target_path(target, name), content)
        prune_empty_managed_dirs(target)
        for name in changing:
            content = desired.get(name)
            path = target_path(target, name)
            if content is None:
                if path_exists_no_follow(path):
                    fail_concurrent(f"managed path appeared after removal: {path}")
            else:
                snapshot = snapshot_file(path, owner_only=True)
                if snapshot is None or snapshot.digest != sha256_bytes(content):
                    fail_concurrent(f"managed path changed after replacement: {path}")
        if postcondition is not None:
            postcondition()
    except BaseException:
        restore_managed_hold(target, hold, moved, desired, changing)
        raise
    if moved and path_exists_no_follow(hold):
        return retire_path_after_commit(root, target, hold, "managed-replace")
    if path_exists_no_follow(hold):
        hold.rmdir()
        fsync_directory(hold.parent)
    return False


def remove_created_target_if_empty(target: Path, existed_before: bool) -> None:
    if existed_before:
        return
    try:
        if target.exists() and target.is_dir() and not any(target.iterdir()):
            target.rmdir()
    except OSError:
        pass


def create_backup(
    target: Path, exclude: int | None = None
) -> tuple[int, dict[str, bytes | None], bool]:
    pool = backup_pool(target)
    ensure_private_directory(pool, create=True)
    pool.chmod(OWNER_DIRECTORY_MODE)
    status = inspect_target(target)
    desired: dict[str, bytes | None] = {name: None for name in MANAGED_PATHS}
    records: dict[str, dict[str, Any] | None] = {}
    for name in ALL_PAYLOAD_PATHS:
        path = target_path(target, name)
        if path_exists_no_follow(path):
            content, _ = read_regular_file(path, f"managed path {path}", owner_only=True)
            desired[name] = content
            records[name] = {
                "path": name,
                "size": len(content),
                "sha256": sha256_bytes(content),
            }
        else:
            records[name] = None
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
        "source_setup_id": status["raw_setup_id"] if status["state"] == "managed" else None,
        "managed_paths": records,
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
            fail(f"backup replacement residue already exists: {replaced}")
        destination_moved = False
        if destination.exists():
            destination.rename(replaced)
            fsync_directory(pool)
            destination_moved = True
        try:
            staging.rename(destination)
            fsync_directory(pool)
        except BaseException:
            if (
                destination_moved
                and path_exists_no_follow(replaced)
                and not path_exists_no_follow(destination)
            ):
                replaced.rename(destination)
                fsync_directory(pool)
            raise
        cleanup_pending = False
        if replaced.exists():
            root = require_control_root(create=True)
            cleanup_pending = retire_path_after_commit(root, target, replaced, "backup-rotate")
    except BaseException:
        if path_exists_no_follow(staging):
            cleanup_path(staging)
            fsync_directory(staging.parent)
        raise
    return slot, desired, cleanup_pending


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
    records = validate_backup_record_map(envelope["managed_paths"], f"backup slot {slot}")
    stamp_digest = envelope["stamp_sha256"]
    if stamp_digest is not None and (
        not isinstance(stamp_digest, str) or SHA256_PATTERN.fullmatch(stamp_digest) is None
    ):
        fail(f"backup slot {slot} stamp_sha256 must be null or a SHA-256 digest")
    payload_root = slot_root / "payload"
    require_directory(payload_root, f"backup slot {slot} payload")
    desired: dict[str, bytes | None] = {name: None for name in MANAGED_PATHS}
    for name, record in records.items():
        if record is None:
            continue
        content, _ = read_regular_file(
            payload_root / name,
            f"backup slot {slot} payload {name}",
            owner_only=True,
        )
        if len(content) != record["size"]:
            fail(f"backup slot {slot} payload size mismatch for {name}")
        if sha256_bytes(content) != record["sha256"]:
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
    target: Path, setup_id: str, profile_id: str, *, preserve_from_current: bool
) -> dict[str, bytes | None]:
    _, rendered = render_setup(setup_id, profile_id)
    if preserve_from_current and path_exists_no_follow(target / "settings.json"):
        current, _ = read_regular_file(target / "settings.json", "managed settings.json")
        current_stamp = load_stamp(target)
        current_profile = (
            profile_for_stamp(target, current_stamp) if current_stamp is not None else profile_id
        )
        rendered["settings.json"] = merge_settings(
            rendered["settings.json"],
            settings_overlay(current, current_profile or profile_id),
        )
    desired: dict[str, bytes | None] = {name: None for name in MANAGED_PATHS}
    for name, content in rendered.items():
        desired[name] = content
    desired[STAMP_NAME] = stamp_bytes(target, setup_id, profile_id, rendered)
    return desired


def plan_setup(target: Path, setup_id: str, profile_id: str) -> dict[str, Any]:
    setup_id, profile_id = resolve_setup_profile(setup_id, profile_id)
    render_setup(setup_id, profile_id)
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
    elif status["setup_id"] == setup_id and status["profile_id"] == profile_id:
        operation = "update"
    else:
        operation = "switch"
    desired = desired_for_setup(
        target,
        setup_id,
        profile_id,
        preserve_from_current=status["state"] == "managed",
    )
    changes: list[str] = []
    for name in MANAGED_PATHS:
        path = target_path(target, name)
        snapshot = snapshot_file(path, owner_only=False)
        current = snapshot.digest if snapshot is not None else None
        content = desired.get(name)
        wanted = sha256_bytes(content) if content is not None else None
        if current != wanted:
            changes.append(name)
    return {
        "schema_version": 1,
        "command": "plan",
        "target": str(target),
        "setup_id": setup_id,
        "profile_id": profile_id,
        "current_profile_id": status["profile_id"],
        "legacy_setup_id": status["legacy_setup_id"],
        "migration_required": status["migration_required"],
        "operation": operation,
        "changes": changes,
        "backup_required": status["state"] == "managed",
        "mutates": False,
        "cleanup_pending": status["cleanup_pending"],
    }


def rollback_to(target: Path, rollback_desired: dict[str, bytes | None]) -> None:
    current = snapshot_managed(target, owner_only=False)
    root = require_control_root(create=True)
    replace_managed_state(target, rollback_desired, current, root=root)


def mutate_setup(target: Path, setup_id: str, profile_id: str, command: str) -> dict[str, Any]:
    setup_id, profile_id = resolve_setup_profile(setup_id, profile_id)
    plan = plan_setup(target, setup_id, profile_id)
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
            "profile_id": profile_id,
            "changed": [],
            "backup_slot": None,
            "cleanup_pending": plan["cleanup_pending"],
        }
    existed_before = target.exists()
    with target_lock(target):
        prior_status = inspect_target(target)
        plan = plan_setup(target, setup_id, profile_id)
        if command == "install" and plan["operation"] == "switch":
            fail("install cannot change setup identity; use switch")
        if command == "switch" and plan["operation"] != "switch":
            fail("switch requires a managed target with a different setup")
        backup_slot: int | None = None
        rollback_desired: dict[str, bytes | None] | None = None
        backup_cleanup_pending = False
        managed_cleanup_pending = False
        before = snapshot_managed(target, owner_only=True)
        if plan["backup_required"]:
            backup_slot, rollback_desired, backup_cleanup_pending = create_backup(target)
            before = snapshot_managed(target, owner_only=True)
        try:
            ensure_private_directory(target, create=True)
            desired = desired_for_setup(
                target,
                setup_id,
                profile_id,
                preserve_from_current=prior_status["state"] == "managed",
            )
            root = require_control_root(create=True)

            def postcondition() -> None:
                final = require_clean_managed(target)
                if final["setup_id"] != setup_id or final["profile_id"] != profile_id:
                    fail("postcondition failed: setup/profile identity mismatch")

            managed_cleanup_pending = replace_managed_state(
                target,
                desired,
                before,
                root=root,
                postcondition=postcondition,
            )
        except BaseException:
            del rollback_desired
            remove_created_target_if_empty(target, existed_before)
            raise
    return {
        "schema_version": 1,
        "command": command,
        "target": str(target),
        "setup_id": setup_id,
        "profile_id": profile_id,
        "changed": plan["changes"],
        "backup_slot": backup_slot,
        "cleanup_pending": backup_cleanup_pending or managed_cleanup_pending,
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
        restore_stamp_content = restore_desired.get(STAMP_NAME)
        if restore_stamp_content is None:
            fail("selected backup is missing the managed Qwen Code stamp")
        restored_stamp = load_stamp_from_bytes(restore_stamp_content, target)
        restored_setup_id = restored_stamp["_content_setup_id"]
        restored_profile_id = restored_stamp["_profile_id"]
        restored_legacy_setup_id = restored_stamp["_legacy_setup_id"]
        before = snapshot_managed(target, owner_only=True)
        rollback_slot, rollback_desired, backup_cleanup_pending = create_backup(
            target, exclude=slot
        )
        managed_cleanup_pending = False
        try:
            ensure_private_directory(target, create=True)
            root = require_control_root(create=True)

            def postcondition() -> None:
                final = require_clean_managed(target)
                if (
                    final["raw_setup_id"] != restored_stamp["setup_id"]
                    or final["profile_id"] != restored_profile_id
                ):
                    fail("postcondition failed: restored setup identity mismatch")

            managed_cleanup_pending = replace_managed_state(
                target,
                restore_desired,
                before,
                root=root,
                postcondition=postcondition,
            )
        except BaseException:
            del rollback_desired
            raise
    return {
        "schema_version": 1,
        "command": "restore",
        "target": str(target),
        "setup_id": restored_setup_id,
        "profile_id": restored_profile_id,
        "legacy_setup_id": restored_legacy_setup_id,
        "restored_backup_slot": slot,
        "rollback_backup_slot": rollback_slot,
        "cleanup_pending": backup_cleanup_pending or managed_cleanup_pending,
    }


def remove_setup(target: Path) -> dict[str, Any]:
    status = require_clean_managed(target)
    with target_lock(target):
        status = require_clean_managed(target)
        before = snapshot_managed(target, owner_only=True)
        backup_slot, rollback_desired, backup_cleanup_pending = create_backup(target)
        managed_cleanup_pending = False
        try:
            desired = {name: None for name in MANAGED_PATHS}
            root = require_control_root(create=True)
            managed_cleanup_pending = replace_managed_state(
                target,
                desired,
                before,
                root=root,
            )
        except BaseException:
            del rollback_desired
            raise
    return {
        "schema_version": 1,
        "command": "remove",
        "target": str(target),
        "removed_setup_id": status["setup_id"],
        "removed_profile_id": status["profile_id"],
        "removed_legacy_setup_id": status["legacy_setup_id"],
        "backup_slot": backup_slot,
        "cleanup_pending": backup_cleanup_pending or managed_cleanup_pending,
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


def launch_environment(target: Path) -> dict[str, str]:
    runtime = create_or_require_private_child_directory(target, Path("runtime"))
    tmp = create_or_require_private_child_directory(target, Path("runtime") / "tmp")
    xdg_config = create_or_require_private_child_directory(target, Path("runtime") / "xdg-config")
    xdg_cache = create_or_require_private_child_directory(target, Path("runtime") / "xdg-cache")
    xdg_state = create_or_require_private_child_directory(target, Path("runtime") / "xdg-state")
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


def user_access(path: Path, mode: int) -> bool:
    try:
        return os.access(path, mode, effective_ids=True)
    except (TypeError, NotImplementedError):
        return os.access(path, mode)


def capture_caller_cwd() -> str:
    try:
        return str(Path.cwd().resolve(strict=True))
    except OSError as exc:
        fail(f"launch workspace could not be captured: {exc}")


def resolve_launch_workspace(raw_workspace: str | None, caller_cwd: str | None) -> Path:
    explicit = raw_workspace is not None
    raw = raw_workspace if explicit else caller_cwd
    if raw is None:
        fail("launch workspace could not be captured")
    if explicit and raw.startswith("~"):
        fail("--workspace must be an absolute path without user expansion")
    workspace = Path(os.path.normpath(raw))
    if not workspace.is_absolute():
        fail("--workspace must be an absolute path")
    try:
        info = workspace.lstat()
    except FileNotFoundError:
        fail(f"launch workspace does not exist: {workspace}")
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        fail(f"launch workspace must be a real directory: {workspace}")
    parents = list(workspace.parents)
    for ancestor in reversed(parents):
        if not user_access(ancestor, os.X_OK):
            fail(f"launch workspace parent is not traversable: {ancestor}")
    if not user_access(workspace, os.R_OK | os.X_OK):
        fail(f"launch workspace is not accessible: {workspace}")
    return workspace


def sri_sha512_bytes(integrity: str) -> bytes:
    algorithm, separator, encoded = integrity.partition("-")
    if algorithm != "sha512" or separator != "-":
        fail("npm package integrity must use sha512 SRI")
    try:
        return base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        fail(f"npm package integrity is not valid base64: {exc}")


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
        fail(f"failed to download official Qwen Code artifact: {exc}")
    return b"".join(chunks)


def download_verified_bytes(
    url: str,
    *,
    expected_size: int,
    max_bytes: int,
    sha256: str | None = None,
    sha1: str | None = None,
    sri_sha512: str | None = None,
    label: str,
) -> bytes:
    content = download_bytes(url, max_bytes=max_bytes)
    if len(content) != expected_size:
        fail(f"{label} size mismatch: expected {expected_size}, got {len(content)}")
    if sha256 is not None and sha256_bytes(content) != sha256:
        fail(f"{label} SHA-256 mismatch")
    if sha1 is not None and hashlib.sha1(content).hexdigest() != sha1:
        fail(f"{label} SHA-1 mismatch")
    if sri_sha512 is not None:
        expected = sri_sha512_bytes(sri_sha512)
        actual = hashlib.sha512(content).digest()
        if actual != expected:
            fail(f"{label} SRI sha512 mismatch")
    return content


def verify_npm_package_provenance() -> dict[str, Any]:
    content = download_verified_bytes(
        QWEN_NPM_TARBALL_URL,
        expected_size=QWEN_NPM_TARBALL_SIZE_BYTES,
        max_bytes=NPM_TARBALL_MAX_BYTES,
        sha1=QWEN_NPM_SHASUM,
        sri_sha512=QWEN_NPM_INTEGRITY,
        label="official npm Qwen Code package",
    )
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            package_member = archive.getmember("package/package.json")
            entrypoint_member = archive.getmember("package/cli-entry.js")
            package_stream = archive.extractfile(package_member)
            if package_stream is None:
                fail("npm package metadata is missing")
            package = parse_json_object(package_stream.read(METADATA_MAX_BYTES + 1), "npm package")
    except (KeyError, tarfile.TarError, OSError) as exc:
        fail(f"official npm package layout is invalid: {exc}")
    require_exact_keys(
        {
            "name": package.get("name"),
            "version": package.get("version"),
            "bin": package.get("bin"),
            "engines": package.get("engines"),
        },
        {"name", "version", "bin", "engines"},
        "npm package selected metadata",
    )
    if package["name"] != QWEN_CODE_PACKAGE:
        fail("npm package identity mismatch")
    if package["version"] != TESTED_QWEN_CODE_VERSION:
        fail("npm package version mismatch")
    if package["bin"] != {QWEN_COMMAND: "cli-entry.js"}:
        fail("npm package bin mapping mismatch")
    if not isinstance(package["engines"], dict) or package["engines"].get("node") != ">=22.0.0":
        fail("npm package Node engine mismatch")
    if not entrypoint_member.isfile() or entrypoint_member.size <= 0:
        fail("npm package CLI entrypoint is invalid")
    return {
        "tarball": QWEN_NPM_TARBALL_URL,
        "size_bytes": QWEN_NPM_TARBALL_SIZE_BYTES,
        "integrity": QWEN_NPM_INTEGRITY,
        "shasum": QWEN_NPM_SHASUM,
        "node_requires": package["engines"]["node"],
        "bin": package["bin"],
    }


def release_archive_metadata_for_host() -> tuple[str, dict[str, Any]]:
    model = preflight_supported_host()
    vendor_key = f"{model.vendor_os}-{model.vendor_arch}"
    metadata = QWEN_RELEASE_ARCHIVES.get(vendor_key)
    if metadata is None:
        fail(f"unsupported Qwen Code release archive platform: {vendor_key}")
    return vendor_key, metadata


def fsync_tree_directories(root: Path) -> None:
    directories = [root]
    directories.extend(path for path in root.rglob("*") if path.is_dir() and not path.is_symlink())
    for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
        fsync_directory(directory)


def safe_extract_release_archive(content: bytes, destination: Path) -> None:
    if path_exists_no_follow(destination):
        fail("Qwen Code archive install destination already exists")
    missing: list[Path] = []
    cursor = destination
    while not path_exists_no_follow(cursor):
        missing.append(cursor)
        cursor = cursor.parent
    require_directory(cursor, "Qwen Code archive destination ancestor")
    for directory in reversed(missing):
        directory.mkdir(mode=OWNER_DIRECTORY_MODE)
        directory.chmod(OWNER_DIRECTORY_MODE)
        fsync_directory(directory.parent)
    path_count = 0
    byte_count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not name.parts:
                    fail(f"Qwen Code archive member escapes install root: {member.name}")
                if name.parts[0] != "qwen-code":
                    fail(f"Qwen Code archive member has unexpected root: {member.name}")
                if len(name.parts) == 1:
                    continue
                relative = Path(*name.parts[1:])
                target = destination / relative
                path_count += 1
                if path_count > SOFTWARE_TREE_MAX_PATHS:
                    fail("Qwen Code archive exceeds software path bound")
                if member.isdir():
                    create_or_require_private_child_directory(destination, relative)
                    continue
                if not member.isfile():
                    fail(f"Qwen Code archive member has unsupported type: {member.name}")
                byte_count += member.size
                if byte_count > SOFTWARE_TREE_MAX_BYTES:
                    fail("Qwen Code archive exceeds software byte bound")
                stream = archive.extractfile(member)
                if stream is None:
                    fail(f"Qwen Code archive file cannot be read: {member.name}")
                data = stream.read()
                if len(data) != member.size:
                    fail(f"Qwen Code archive member size mismatch: {member.name}")
                mode = 0o700 if member.mode & 0o111 else OWNER_FILE_MODE
                create_or_require_private_child_directory(destination, relative.parent)
                durable_write_file(target, data, mode)
    except tarfile.TarError as exc:
        fail(f"official Qwen Code release archive is invalid: {exc}")
    fsync_tree_directories(destination)


def materialize_official_release_archive(stage_root: Path, live_stage: Path) -> dict[str, Any]:
    del stage_root
    npm_provenance = verify_npm_package_provenance()
    vendor_key, metadata = release_archive_metadata_for_host()
    asset = metadata["asset"]
    archive_url = f"{QWEN_RELEASE_BASE_URL}/{asset}"
    content = download_verified_bytes(
        archive_url,
        expected_size=metadata["size_bytes"],
        max_bytes=RELEASE_ARCHIVE_MAX_BYTES,
        sha256=metadata["sha256"],
        label=f"official Qwen Code release archive {asset}",
    )
    install_root = live_stage / SOFTWARE_DIR_RELATIVE
    safe_extract_release_archive(content, install_root)
    chmod_private_tree(live_stage)
    write_target_relative_qwen_launcher(live_stage)
    metadata_json = package_metadata(live_stage)
    if metadata_json["version"] != TESTED_QWEN_CODE_VERSION:
        fail("release archive package version mismatch")
    require_safe_executable(
        live_stage / SOFTWARE_DIR_RELATIVE / "bin" / QWEN_COMMAND,
        live_stage,
        "Qwen Code release archive entrypoint",
    )
    for relative in (
        SOFTWARE_DIR_RELATIVE / "lib" / "cli-entry.js",
        SOFTWARE_DIR_RELATIVE / "manifest.json",
    ):
        require_regular_file(live_stage / relative, f"Qwen Code release archive {relative}")
    return {
        "vendor_platform": vendor_key,
        "asset": asset,
        "url": archive_url,
        "size_bytes": metadata["size_bytes"],
        "sha256": metadata["sha256"],
        "npm": npm_provenance,
    }


def write_target_relative_qwen_launcher(root: Path) -> None:
    launcher = root / "bin" / QWEN_COMMAND
    package_entrypoint = root / SOFTWARE_DIR_RELATIVE / "bin" / QWEN_COMMAND
    require_safe_executable(package_entrypoint, root, "Qwen Code package entrypoint")
    create_or_require_private_child_directory(root, Path("bin"))
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
        launcher.chmod(0o700)
        fsync_directory(launcher.parent)
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
                fail(f"installed Qwen Code tree exceeds the {SOFTWARE_TREE_MAX_PATHS}-path limit")
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
    vendor_key, metadata = release_archive_metadata_for_host()
    return {
        "schema_version": 1,
        "product_name": PRODUCT_NAME,
        "package": QWEN_CODE_PACKAGE,
        "install_method": "official-release-archive",
        "release_archive_platform": vendor_key,
        "release_archive_asset": metadata["asset"],
        "release_archive_url": f"{QWEN_RELEASE_BASE_URL}/{metadata['asset']}",
        "release_archive_size_bytes": metadata["size_bytes"],
        "release_archive_sha256": metadata["sha256"],
        "npm_tarball": QWEN_NPM_TARBALL_URL,
        "npm_tarball_size_bytes": QWEN_NPM_TARBALL_SIZE_BYTES,
        "npm_integrity": QWEN_NPM_INTEGRITY,
        "npm_shasum": QWEN_NPM_SHASUM,
        "executable": f"bin/{QWEN_COMMAND}",
        "entrypoint_resolution": "target-relative-wrapper",
        "install_root": str(SOFTWARE_DIR_RELATIVE),
    }


def build_software_manifest(root: Path) -> dict[str, Any]:
    return {**software_manifest_identity(), **compute_software_tree_digest(root)}


def software_presence(target: Path) -> dict[str, Any]:
    replace_paths_present = [
        str(relative)
        for relative in SOFTWARE_REPLACE_PATHS
        if path_exists_no_follow(target / relative)
    ]
    owned_parent_paths_present = [
        str(relative)
        for relative in SOFTWARE_PARENT_PATHS
        if path_exists_no_follow(target / relative)
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
    root = require_control_root(create=False)
    cleanup_pending = cleanup_pending_for_target(root, target, read_only=True)
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
            "cleanup_pending": cleanup_pending,
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
            "cleanup_pending": cleanup_pending,
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
            "cleanup_pending": cleanup_pending,
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
        "cleanup_pending": cleanup_pending,
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
    fsync_directory(destination.parent)
    fsync_directory(source.parent)


def move_old_path(source: Path, saved: Path) -> None:
    missing: list[Path] = []
    cursor = saved.parent
    while not path_exists_no_follow(cursor):
        missing.append(cursor)
        cursor = cursor.parent
    require_directory(cursor, "software rollback ancestor")
    for directory in reversed(missing):
        directory.mkdir(mode=OWNER_DIRECTORY_MODE)
        directory.chmod(OWNER_DIRECTORY_MODE)
        fsync_directory(directory.parent)
    info = require_directory(saved.parent, "software rollback parent")
    if not is_owner_private_directory(info):
        fail("software rollback parent must be private to the current user")
    os.replace(source, saved)
    fsync_directory(source.parent)
    fsync_directory(saved.parent)


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


def replace_software_state(target: Path, live_stage: Path, hold_parent: Path) -> bool:
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
    committed = False
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
        manifest = load_json_object(
            software_manifest_path(target),
            "Qwen Code software manifest",
            owner_only=True,
        )
        expected = build_software_manifest(target)
        if manifest != expected or manifest.get("version") != TESTED_QWEN_CODE_VERSION:
            fail("installed Qwen Code CLI did not validate as the tested release archive version")
        committed = True
    except BaseException:
        moved_old = [
            relative
            for relative in SOFTWARE_REPLACE_PATHS
            if path_exists_no_follow(hold / relative)
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
    if committed and path_exists_no_follow(hold):
        root = require_control_root(create=True)
        return retire_path_after_commit(root, target, hold, "software-replace")
    return False


def write_stage_software_manifest(live_stage: Path) -> None:
    manifest = live_stage / SOFTWARE_MANIFEST_RELATIVE
    create_or_require_private_child_directory(live_stage, SOFTWARE_MANIFEST_RELATIVE.parent)
    durable_write_file(
        manifest, canonical_json(build_software_manifest(live_stage)), OWNER_FILE_MODE
    )


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
                "cleanup_pending": preflight["cleanup_pending"],
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
                        "cleanup_pending": status["cleanup_pending"],
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
            materialize_official_release_archive(staging, live_stage)
            chmod_private_tree(live_stage)
            write_stage_software_manifest(live_stage)
            cleanup_pending = replace_software_state(target, live_stage, staging)
            installation = require_current_software(target)
        except BaseException:
            remove_created_target_if_empty(target, target_existed_before)
            raise
        finally:
            if staging is not None:
                if path_exists_no_follow(staging):
                    cleanup_path(staging)
                    fsync_directory(staging.parent)
    return {
        "schema_version": 1,
        "command": command,
        "target": str(target),
        "changed": True,
        "version": installation["version"],
        "executable": installation["executable"],
        "cleanup_pending": cleanup_pending,
    }


def remove_cli(target: Path) -> dict[str, Any]:
    require_private_target_directory_for_software(target, allow_missing=True)
    status = software_status(target)
    if status["software_state"] == "absent":
        return {
            "schema_version": 1,
            "command": "remove-cli",
            "target": str(target),
            "changed": False,
            "version": None,
            "cleanup_pending": status["cleanup_pending"],
        }
    validate_existing_software_surface(target)
    hold_parent = target / ".nddev-qwen-code-software-remove"
    if path_exists_no_follow(hold_parent):
        fail("software remove transaction residue already exists")
    hold_parent.mkdir(mode=OWNER_DIRECTORY_MODE)
    hold_parent.chmod(OWNER_DIRECTORY_MODE)
    fsync_directory(hold_parent.parent)
    moved: list[Path] = []
    try:
        for relative in SOFTWARE_REPLACE_PATHS:
            source = target / relative
            if path_exists_no_follow(source):
                saved = hold_parent / relative
                move_old_path(source, saved)
                moved.append(relative)
        for relative in sorted(
            SOFTWARE_PARENT_PATHS, key=lambda item: len(item.parts), reverse=True
        ):
            parent = target / relative
            if path_exists_no_follow(parent):
                with contextlib.suppress(OSError):
                    parent.rmdir()
                    fsync_directory(parent.parent)
        after = software_presence(target)
        if after["software_state"] != "absent":
            fail("postcondition failed: Qwen Code software is still present")
    except BaseException:
        for relative in reversed(moved):
            saved = hold_parent / relative
            if path_exists_no_follow(saved):
                move_replace_path(saved, target / relative)
        with contextlib.suppress(OSError):
            cleanup_path(hold_parent)
        raise
    root = require_control_root(create=True)
    pending = retire_path_after_commit(root, target, hold_parent, "software-remove")
    return {
        "schema_version": 1,
        "command": "remove-cli",
        "target": str(target),
        "changed": True,
        "version": status.get("version"),
        "cleanup_pending": pending,
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
        "cleanup_pending": status["cleanup_pending"],
    }


def sh_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def launch_source_identity(path: Path, root: Path) -> tuple[os.stat_result, str]:
    before = require_safe_executable(path, root, "Qwen Code launch source")
    digest = digest_regular_file(path, "Qwen Code launch source", {"value": 0})
    after = require_safe_executable(path, root, "Qwen Code launch source")
    if identity_of(before) != identity_of(after):
        fail_concurrent("Qwen Code launch source changed during validation")
    return after, digest


def validate_launch_image(image: LaunchImage) -> None:
    info = require_regular_file(image.executable, "Qwen Code launch handoff")
    if hasattr(os, "geteuid") and owner_of(info) != os.geteuid():
        fail("Qwen Code launch handoff must be owned by the current user")
    if stat.S_IMODE(info.st_mode) != 0o500:
        fail("Qwen Code launch handoff must be non-writable with mode 0500")
    if identity_of(info) != image.inode:
        fail_concurrent("Qwen Code launch handoff identity changed")
    content, reopened = read_regular_file(
        image.executable,
        "Qwen Code launch handoff",
        max_bytes=METADATA_MAX_BYTES,
    )
    if identity_of(reopened) != image.inode or sha256_bytes(content) != image.digest:
        fail_concurrent("Qwen Code launch handoff changed while being read")


def prepare_launch_image(target: Path, executable: Path) -> LaunchImage:
    source_info, source_digest = launch_source_identity(executable, target)
    launch_parent = create_or_require_private_child_directory(
        target,
        Path("runtime") / "launch-images",
    )
    image_root = launch_parent / f".nddev-qwen-code-launch.{os.getpid()}.{uuid.uuid4().hex}"
    image_root.mkdir(mode=OWNER_DIRECTORY_MODE)
    fsync_directory(launch_parent)
    handoff = image_root / QWEN_COMMAND
    script = (f'#!/bin/sh\nset -eu\nexec {sh_single_quote(str(executable))} "$@"\n').encode("utf-8")
    durable_write_file(handoff, script, 0o500)
    image_root.chmod(0o500)
    fsync_directory(launch_parent)
    info = require_regular_file(handoff, "Qwen Code launch handoff")
    image = LaunchImage(
        root=image_root,
        executable=handoff,
        digest=sha256_bytes(script),
        inode=identity_of(info),
    )
    validate_launch_image(image)
    current_info, current_digest = launch_source_identity(executable, target)
    if identity_of(current_info) != identity_of(source_info) or current_digest != source_digest:
        fail_concurrent("Qwen Code launch source changed before handoff")
    return image


def cleanup_launch_image(image: LaunchImage) -> None:
    if not path_exists_no_follow(image.root):
        return
    image.root.chmod(OWNER_DIRECTORY_MODE)
    if path_exists_no_follow(image.executable):
        image.executable.unlink()
        fsync_directory(image.root)
    image.root.rmdir()
    fsync_directory(image.root.parent)


def spawn_qwen_child(
    executable: Path,
    child_args: list[str],
    environment: dict[str, str],
    workspace: Path,
    revalidate: Any,
) -> int:
    try:
        revalidate()
        process = subprocess.Popen(
            [str(executable), *child_args],
            env=environment,
            cwd=str(workspace),
        )
        revalidate()
    except FileNotFoundError:
        fail("qwen executable disappeared before launch")
    returncode = process.wait()
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def first_qwen_scope_override(child_args: list[str]) -> str | None:
    """Return the first official Qwen flag that would override managed scope."""
    for argument in child_args:
        if argument == "--":
            return None
        if argument in QWEN_SCOPE_FLAGS_WITHOUT_VALUE or argument in QWEN_SCOPE_FLAGS_WITH_VALUE:
            return argument
        for flag in QWEN_SCOPE_SHORT_FLAGS_WITH_VALUE:
            if argument.startswith(flag) and argument != flag:
                return flag
        if argument.startswith("--"):
            flag = argument.split("=", 1)[0]
            if flag in QWEN_SCOPE_FLAGS_WITH_VALUE and "=" in argument:
                return flag
    return None


def launch_qwen(target: Path, child_args: list[str], workspace: Path) -> int:
    forwarded = child_args[1:] if child_args[:1] == ["--"] else child_args
    override = first_qwen_scope_override(forwarded)
    if override is not None:
        fail(f"launch arguments must not override target-owned Qwen Code scope: {override}")
    require_clean_managed(target)
    installation = require_current_software(target)
    environment = launch_environment(target)
    executable = Path(str(installation["executable"]))
    source_info, source_digest = launch_source_identity(executable, target)
    image = prepare_launch_image(target, executable)

    def revalidate_launch() -> None:
        validate_launch_image(image)
        current_info, current_digest = launch_source_identity(executable, target)
        if identity_of(current_info) != identity_of(source_info) or current_digest != source_digest:
            fail_concurrent("Qwen Code launch source changed before child handoff")

    try:
        return spawn_qwen_child(
            image.executable,
            forwarded,
            environment,
            workspace,
            revalidate_launch,
        )
    finally:
        cleanup_launch_image(image)


def coordinated_target_read_locked(root: Path, lexical: Path, callback: Any) -> Any:
    product_anchor = product_anchor_path(root)
    target_context: Any = None
    target: Path | None = None
    with anchor_lock(product_anchor, kind="product", target=None, exclusive=False, create=False):
        target = canonicalize_target(lexical)
        target_anchor = target_anchor_path(root, target)
        if path_exists_no_follow(target_anchor):
            target_context = anchor_lock(
                target_anchor,
                kind="target",
                target=target,
                exclusive=False,
                create=False,
            )
            target_context.__enter__()
        else:
            ensure_no_anchor_publication_stages(target_anchor)
            return callback(target)
    if target_context is None or target is None:
        fail_concurrent("target coordination handoff failed")
    try:
        return callback(target)
    finally:
        target_context.__exit__(None, None, None)


def coordinated_target_read(raw_target: str, callback: Any) -> Any:
    preflight_supported_host()
    lexical = validate_lexical_target(raw_target)
    for _attempt in range(2):
        root = require_control_root(create=False)
        if root is not None and path_exists_no_follow(product_anchor_path(root)):
            return coordinated_target_read_locked(root, lexical, callback)
        before = cold_read_namespace_snapshot(root)
        if before.state == "anchored":
            if root is None:
                fail_concurrent("Qwen Code control namespace appeared during cold read")
            return coordinated_target_read_locked(root, lexical, callback)
        target = canonicalize_target(lexical)
        result = callback(target)
        root_after = require_control_root(create=False)
        after = cold_read_namespace_snapshot(root_after)
        if after.state == "anchored":
            if root_after is None:
                fail_concurrent("Qwen Code control namespace appeared during cold read")
            return coordinated_target_read_locked(root_after, lexical, callback)
        if after == before:
            return result
    fail_concurrent("Qwen Code control namespace changed during cold read")


def coordinated_target_mutation(raw_target: str, callback: Any) -> Any:
    preflight_supported_host()
    lexical = validate_lexical_target(raw_target)
    root_path = control_root_path()
    root_creation: CreatedDirectorySignature | None = None
    product_anchor = product_anchor_path(root_path)
    target_context: Any = None
    target: Path | None = None
    try:
        root_creation = ensure_private_directory_component_held(root_path)
        root = require_control_root(create=False)
        if root is None:
            fail_concurrent("Qwen Code control root creation failed")
        product_anchor = product_anchor_path(root)
        with anchor_lock(product_anchor, kind="product", target=None, exclusive=True, create=True):
            target = canonicalize_target(lexical)
            targets_root = root / TARGET_ANCHOR_DIRECTORY
            targets_creation = ensure_private_directory_component_held(targets_root)
            target_anchor = target_anchor_path(root, target)
            try:
                target_context = anchor_lock(
                    target_anchor,
                    kind="target",
                    target=target,
                    exclusive=True,
                    create=True,
                )
                target_context.__enter__()
            except BaseException:
                if not path_exists_no_follow(target_anchor):
                    rollback_created_directory(targets_creation, f"target anchor directory {targets_root}")
                raise
            finally:
                close_created_directory_signature(targets_creation)
    except BaseException:
        if not path_exists_no_follow(product_anchor):
            rollback_created_directory(root_creation, f"Qwen Code control root {root_path}")
        raise
    finally:
        close_created_directory_signature(root_creation)
    if target_context is None or target is None:
        fail_concurrent("target coordination handoff failed")
    try:
        drain_cleanup(root, target, read_only=False)
        return callback(target)
    finally:
        target_context.__exit__(None, None, None)


def human_output(value: dict[str, Any]) -> str:
    command = value.get("command")
    if command == "list":
        setups = ", ".join(item["id"] for item in value["setups"])
        profiles = ", ".join(item["id"] for item in value["profiles"])
        return (
            f"setups: {setups}; profiles: {profiles}; default profile: {value['default_profile']}"
        )
    if command == "status":
        setup = f" ({value['setup_id']})" if value["setup_id"] else ""
        profile = f" profile={value['profile_id']}" if value.get("profile_id") else ""
        drift = f"; drift={','.join(value['drift'])}" if value["drift"] else ""
        return f"{value['state']}{setup}{profile}: {value['target']}{drift}"
    if command == "plan":
        changes = ", ".join(value["changes"]) or "none"
        return (
            f"{value['operation']} {value['setup_id']} profile={value['profile_id']} "
            f"at {value['target']}; changes: {changes}"
        )
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
    parser = JsonArgumentParser(
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
        command_parser.add_argument("--setup")
        command_parser.add_argument("--profile", choices=PROFILE_IDS)
        add_target(command_parser)

    restore_parser = subparsers.add_parser("restore", help="Restore a target-bound backup.")
    restore_parser.add_argument("--backup", required=True, type=int, choices=range(10))
    add_target(restore_parser)

    remove_parser = subparsers.add_parser("remove", help="Remove only managed setup files.")
    add_target(remove_parser)

    for command, help_text in (
        ("builder-status", "Inspect the native nddev-builder Qwen extension."),
        ("software-status", "Inspect target-owned Qwen Code CLI software."),
        ("install-cli", "Install the pinned official Qwen Code release archive."),
        ("update-cli", "Update target-owned Qwen Code to the pinned release archive."),
        ("remove-cli", "Remove target-owned Qwen Code CLI software."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        add_target(command_parser)

    launch_parser = subparsers.add_parser(
        "launch", help="Launch Qwen Code with isolated QWEN_HOME."
    )
    add_target(launch_parser)
    launch_parser.add_argument(
        "--workspace",
        help="Existing absolute project directory used as the launched child cwd.",
    )
    launch_parser.add_argument(
        "qwen_args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded to Qwen Code after --.",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any] | int:
    if args.command == "list":
        return {
            "schema_version": 1,
            "command": "list",
            "setups": list_setups(),
            "profiles": list_profiles(),
            "default_profile": DEFAULT_PROFILE_ID,
        }
    if args.command == "status":
        return coordinated_target_read(
            args.target,
            lambda target: {
                "schema_version": 1,
                "command": "status",
                "target": str(target),
                **inspect_target(target),
            },
        )
    if args.command == "plan":
        return coordinated_target_read(
            args.target,
            lambda target: plan_setup(target, args.setup, args.profile),
        )
    if args.command in {"install", "switch"}:
        return coordinated_target_mutation(
            args.target,
            lambda target: mutate_setup(target, args.setup, args.profile, args.command),
        )
    if args.command == "restore":
        return coordinated_target_mutation(
            args.target,
            lambda target: restore_slot(target, args.backup),
        )
    if args.command == "remove":
        return coordinated_target_mutation(args.target, remove_setup)
    if args.command == "builder-status":
        return coordinated_target_read(args.target, builder_status)
    if args.command == "software-status":
        return coordinated_target_read(args.target, software_status)
    if args.command in {"install-cli", "update-cli"}:
        return coordinated_target_mutation(
            args.target,
            lambda target: install_or_update_cli(target, args.command),
        )
    if args.command == "remove-cli":
        return coordinated_target_mutation(args.target, remove_cli)
    if args.command == "launch":
        workspace = resolve_launch_workspace(
            args.workspace,
            getattr(args, "caller_cwd", None),
        )
        return coordinated_target_mutation(
            args.target,
            lambda target: launch_qwen(target, list(args.qwen_args), workspace),
        )
    fail(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "launch" and args.workspace is None:
            args.caller_cwd = capture_caller_cwd()
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
