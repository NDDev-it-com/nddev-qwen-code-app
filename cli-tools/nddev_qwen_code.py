#!/usr/bin/env python3
"""Transactional setup manager for a caller-selected Qwen Code home."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
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
MANAGED_FILES = ("settings.json", "QWEN.md", "AGENTS.md", "CLAUDE.md")
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
TESTED_QWEN_CODE_VERSION = "0.21.1"
QWEN_CODE_PACKAGE = "@qwen-code/qwen-code"
QWEN_COMMAND = "qwen"
INSTALLER_URL = (
    "https://qwen-code-assets.oss-cn-hangzhou.aliyuncs.com/installation/install-qwen-standalone.sh"
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
CONTROL_ROOT_ENV = "NDDEV_QWEN_CODE_CONTROL_ROOT"
CONTROL_ROOT_RELATIVE = Path(".nddev") / "qwen-code"
PRODUCT_ANCHOR_NAME = "product.lock"
TARGET_ANCHOR_DIRECTORY = "targets"
TARGET_ANCHOR_SUFFIX = ".lock"
ANCHOR_PUBLICATION_PREFIX = ".nddev-qwen-code-publish-"
CLEANUP_DIRECTORY_NAME = "cleanup"
CLEANUP_PREPARE_NAME = "prepare.json"
CLEANUP_JOURNAL_NAME = "pending.json"
CLEANUP_TOMBSTONE_DIRECTORY = "tombstones"
CLEANUP_MAX_TREE_ENTRIES = 2048
CLEANUP_MAX_SERIALIZED_BYTES = 1024 * 1024
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
class HostModel:
    product_host_id: str
    vendor_os: str
    vendor_arch: str
    unsupported_category: str | None = None


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
    configured = os.environ.get(CONTROL_ROOT_ENV)
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            fail(f"{CONTROL_ROOT_ENV} must be an absolute path")
        return root
    return Path.home() / CONTROL_ROOT_RELATIVE


def ensure_private_directory_component(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        parent = path.parent
        require_directory(parent, f"{path.name} parent")
        path.mkdir(mode=OWNER_DIRECTORY_MODE)
        path.chmod(OWNER_DIRECTORY_MODE)
        fsync_directory(parent)
        info = path.lstat()
    if not is_owner_private_directory(info):
        fail(f"{path} must be a private manager-owned directory")


def require_control_root(*, create: bool) -> Path | None:
    root = control_root_path()
    try:
        info = root.lstat()
    except FileNotFoundError:
        if not create:
            return None
        parents = [root]
        cursor = root.parent
        while not cursor.exists():
            parents.append(cursor)
            cursor = cursor.parent
        require_directory(cursor, "control root ancestor")
        for directory in reversed(parents):
            directory.mkdir(mode=OWNER_DIRECTORY_MODE)
            directory.chmod(OWNER_DIRECTORY_MODE)
            fsync_directory(directory.parent)
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


def publication_aliases_for(path: Path, info: os.stat_result) -> list[Path]:
    aliases: list[Path] = []
    try:
        entries = sorted(path.parent.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        fail(f"cannot enumerate coordination namespace {path.parent}: {exc}")
    prefix = f"{ANCHOR_PUBLICATION_PREFIX}{path.name}."
    for entry in entries:
        if not entry.name.startswith(prefix) or not entry.name.endswith(".tmp"):
            continue
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
    recover_alias: bool = False,
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
        if not recover_alias:
            fail(f"coordination anchor {path} has an incomplete publication alias")
        aliases = publication_aliases_for(path, info)
        if len(aliases) != 1 or info.st_nlink != 2:
            fail(f"coordination anchor {path} has unknown hard-link aliases")
        aliases[0].unlink()
        fsync_directory(path.parent)
        content, info = read_regular_file(
            path,
            f"coordination anchor {path}",
            owner_only=True,
            max_bytes=METADATA_MAX_BYTES,
            allow_hardlinks=True,
        )
        validate_anchor_content(path, content, kind, target)
        if info.st_nlink != 1:
            fail(f"coordination anchor {path} alias recovery did not converge")
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
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), OWNER_FILE_MODE)
        try:
            os.link(temporary, path)
        except FileExistsError:
            temporary.unlink()
            fsync_directory(path.parent)
            return
        except OSError as exc:
            temporary.unlink()
            fsync_directory(path.parent)
            raise QwenCodeSetupError(f"coordination anchor publication failed: {exc}") from exc
        try:
            temporary.unlink()
        finally:
            fsync_directory(path.parent)
    finally:
        if path_exists_no_follow(temporary):
            try:
                temporary.unlink()
                fsync_directory(temporary.parent)
            except OSError:
                pass


@contextlib.contextmanager
def anchor_lock(
    path: Path,
    *,
    kind: str,
    target: Path | None,
    exclusive: bool,
    create: bool,
) -> Iterator[None]:
    if create:
        publish_no_replace_file(path, anchor_payload(kind, target=target))
    validate_anchor_path(path, kind=kind, target=target, recover_alias=exclusive)
    descriptor = open_no_follow(path, os.O_RDWR)
    try:
        opened = os.fstat(descriptor)
        current = validate_anchor_path(path, kind=kind, target=target, recover_alias=exclusive)
        if identity_of(opened) != identity_of(current):
            fail_concurrent(f"coordination anchor {path} changed during open")
        fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        locked = os.fstat(descriptor)
        if identity_of(locked) != identity_of(current):
            fail_concurrent(f"coordination anchor {path} changed during lock")
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


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
    rendered: dict[str, bytes] = {"settings.json": canonical_json(settings)}
    for name in ("QWEN.md", "AGENTS.md", "CLAUDE.md"):
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
            }
        )
    if not entries:
        fail("setup catalog is empty")
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
    root = require_control_root(create=False)
    cleanup_pending = cleanup_pending_for_target(root, target, read_only=True)
    if not ensure_private_directory(target, create=False):
        return {
            "state": "missing",
            "setup_id": None,
            "build_version": None,
            "drift": [],
            "unmanaged_managed_paths": [],
            "builder_extension": "missing",
            "cleanup_pending": cleanup_pending,
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
            "cleanup_pending": cleanup_pending,
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
    if path_exists_no_follow(paths["prepare"]):
        if read_only:
            fail("cleanup prepare intent is pending")
        return True
    if not path_exists_no_follow(paths["pending"]):
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
            unlink_tree_bottom_up(tombstone)
    if any(tombstone_root.iterdir()):
        fail("cleanup tombstone parent did not drain completely")
    with contextlib.suppress(FileNotFoundError):
        paths["pending"].unlink()
        fsync_directory(paths["root"])
    with contextlib.suppress(FileNotFoundError):
        paths["prepare"].unlink()
        fsync_directory(paths["root"])
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
        if path_exists_no_follow(tombstone) and not path_exists_no_follow(path):
            with contextlib.suppress(OSError):
                tombstone.rename(path)
                fsync_directory(path.parent)
                fsync_directory(tombstone.parent)
        raise
    try:
        drain_cleanup(root, target, read_only=False)
    except BaseException:
        return True
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
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp_path.chmod(mode)
        os.replace(temp_path, path)
        path.chmod(mode)
        fsync_directory(path.parent)
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


def create_backup(
    target: Path, exclude: int | None = None
) -> tuple[int, dict[str, bytes | None], bool]:
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
            fail(f"backup replacement residue already exists: {replaced}")
        if destination.exists():
            destination.rename(replaced)
        staging.rename(destination)
        fsync_directory(pool)
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
        "cleanup_pending": status["cleanup_pending"],
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
            "cleanup_pending": plan["cleanup_pending"],
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
            backup_slot, rollback_desired, backup_cleanup_pending = create_backup(target)
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
        "cleanup_pending": backup_cleanup_pending if backup_slot is not None else False,
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
        rollback_slot, rollback_desired, backup_cleanup_pending = create_backup(
            target, exclude=slot
        )
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
        "cleanup_pending": backup_cleanup_pending,
    }


def remove_setup(target: Path) -> dict[str, Any]:
    status = require_clean_managed(target)
    with target_lock(target):
        status = require_clean_managed(target)
        before = snapshot_managed(target, owner_only=True)
        backup_slot, rollback_desired, backup_cleanup_pending = create_backup(target)
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
        "cleanup_pending": backup_cleanup_pending,
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
    home = create_or_require_private_child_directory(stage_root, Path("home"))
    tmp = create_or_require_private_child_directory(stage_root, Path("tmp"))
    xdg_config = create_or_require_private_child_directory(stage_root, Path("xdg-config"))
    xdg_cache = create_or_require_private_child_directory(stage_root, Path("xdg-cache"))
    xdg_state = create_or_require_private_child_directory(stage_root, Path("xdg-state"))
    qwen_home = create_or_require_private_child_directory(stage_root, Path("qwen-home"))
    qwen_runtime = create_or_require_private_child_directory(stage_root, Path("qwen-runtime"))
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
    durable_write_file(installer, content, 0o700)
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
        status = software_status(target)
        if not status["installed"] or not status["current"]:
            fail("installed Qwen Code CLI did not validate as the tested standalone version")
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
            run_official_standalone_installer(staging, live_stage)
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
        after = software_status(target)
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
    require_clean_managed(target)
    installation = require_current_software(target)
    environment = launch_environment(target)
    executable = str(installation["executable"])
    return spawn_qwen_child(executable, forwarded, environment)


def coordinated_target_read(raw_target: str, callback: Any) -> Any:
    preflight_supported_host()
    lexical = validate_lexical_target(raw_target)
    root = require_control_root(create=False)
    if root is None or not path_exists_no_follow(product_anchor_path(root)):
        target = canonicalize_target(lexical)
        result = callback(target)
        root_after = require_control_root(create=False)
        if root_after is None or not path_exists_no_follow(product_anchor_path(root_after)):
            return result
        root = root_after
    product_anchor = product_anchor_path(root)
    with anchor_lock(product_anchor, kind="product", target=None, exclusive=False, create=False):
        target = canonicalize_target(lexical)
        target_anchor = target_anchor_path(root, target)
        if path_exists_no_follow(target_anchor):
            with anchor_lock(
                target_anchor,
                kind="target",
                target=target,
                exclusive=False,
                create=False,
            ):
                return callback(target)
        return callback(target)


def coordinated_target_mutation(raw_target: str, callback: Any) -> Any:
    preflight_supported_host()
    lexical = validate_lexical_target(raw_target)
    root = require_control_root(create=True)
    product_anchor = product_anchor_path(root)
    with anchor_lock(product_anchor, kind="product", target=None, exclusive=True, create=True):
        target = canonicalize_target(lexical)
        targets_root = root / TARGET_ANCHOR_DIRECTORY
        ensure_private_directory_component(targets_root)
        target_anchor = target_anchor_path(root, target)
        with anchor_lock(
            target_anchor,
            kind="target",
            target=target,
            exclusive=True,
            create=True,
        ):
            drain_cleanup(root, target, read_only=False)
            return callback(target)


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
        ("remove-cli", "Remove target-owned Qwen Code CLI software."),
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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any] | int:
    if args.command == "list":
        return {"schema_version": 1, "command": "list", "setups": list_setups()}
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
        return coordinated_target_read(args.target, lambda target: plan_setup(target, args.setup))
    if args.command in {"install", "switch"}:
        return coordinated_target_mutation(
            args.target,
            lambda target: mutate_setup(target, args.setup, args.command),
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
        return coordinated_target_mutation(
            args.target,
            lambda target: launch_qwen(target, list(args.qwen_args)),
        )
    fail(f"unsupported command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
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
