"""Bounded, offline preparation of complete-repository dataset cases.

This module never installs, imports or executes a target repository. The
``pristine`` label means the operator's unmodified input, not verified health.
Version claims remain operator supplied until an independent preflight checks
the snapshot against upstream and executes the frozen evaluation contract.
Directory separation is not an operating-system sandbox.
"""
from __future__ import annotations

from dataclasses import dataclass
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
from typing import Any


SCHEMA = "testfix.dataset-case.v1"
TRACKS = {"controlled_defect_repair", "requirement_implementation"}
DOMAINS = {"web", "software", "mcu", "mpu"}
_EXCLUDED_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox", ".nox",
    ".pnpm-store", ".next", ".nuxt", ".gradle", ".idea", ".vscode", ".pio",
    "evaluator-only", ".testfix-private", ".codex", ".ssh", ".aws", ".azure",
}
_SECRET_NAMES = {"id_rsa", "id_ed25519", ".npmrc", ".pypirc", "credentials", "credentials.json", "auth.json"}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
_ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}
_TEST_DIRS = {"test", "tests", "__tests__", "testsuite", "testsuites", "testcases", "test_suite", "test_suites", "spec", "specs", "e2e", "acceptance", "acceptance_tests",
              "regression_tests", "generated_tests", "evaluation", "evaluator-only", "testdata", "fixtures"}
_LOCK_NAMES = {"package-lock.json", "npm-shrinkwrap.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
               "go.sum", "requirements.txt", "pipfile.lock", "pylock.toml", "west.yml", "west.yaml", ".gitmodules",
               "idf_component.yml", "idf_component.yaml"}
_TEST_CONFIG = {"conftest.py", "pytest.ini", "tox.ini", ".coveragerc", "jest.config.js", "jest.config.ts",
                "vitest.config.js", "vitest.config.ts", "playwright.config.js", "playwright.config.ts"}
_SPEC_REQUIRED = {"schema", "id", "track", "domain", "source", "requirements", "allowed_changes"}
_SPEC_OPTIONAL = {"title", "mutations", "protected_paths"}


@dataclass(frozen=True)
class PreparationLimits:
    max_files: int = 50_000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024

    def __post_init__(self):
        if (type(self.max_files) is not int or not 1 <= self.max_files <= 200_000
                or type(self.max_file_bytes) is not int or not 1 <= self.max_file_bytes <= 128 * 1024 * 1024
                or type(self.max_total_bytes) is not int or not 1 <= self.max_total_bytes <= 2 * 1024 * 1024 * 1024):
            raise ValueError("Preparation limits must be bounded positive integers")


def _fields(value: Any, required: set[str], optional: set[str], label: str) -> dict:
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError(f"{label} has missing or unsupported fields")
    return value


def _text(value: Any, label: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"{label} must be bounded nonempty text")
    return value


def _relative(value: Any, *, pattern: bool = False) -> str:
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value or ":" in value:
        raise ValueError("Repository path must be a bounded relative POSIX path")
    if (PurePosixPath(value).is_absolute() or PureWindowsPath(value).drive
            or any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in value.split("/"))
            or any(ord(char) < 32 for char in value) or any(char in value for char in '<>|"')
            or (not pattern and any(char in value for char in "*?"))):
        raise ValueError("Repository path cannot escape or use ambiguous path spelling")
    # Device names and ADS are special even when a dataset is prepared on Linux.
    for part in value.split("/"):
        stem = part.split(".")[0].upper()
        if stem in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
            raise ValueError("Repository path contains a reserved device name")
    return value


def _excluded(value: str, *, directory: bool = False) -> bool:
    parts = [part.lower() for part in value.split("/")]
    if any(part in _EXCLUDED_DIRS or part.startswith(".venv-") for part in parts):
        return True
    name = parts[-1]
    if name.startswith(".env") and name not in _ENV_TEMPLATES:
        return True
    return (not directory and (name in _SECRET_NAMES or Path(name).suffix in _SECRET_SUFFIXES
                              or name.endswith((".pyc", ".pyo"))))


def _protected(value: str, extra: list[str]) -> bool:
    parts = value.lower().split("/")
    name = parts[-1]
    return (any(part in _TEST_DIRS for part in parts[:-1]) or name in _TEST_CONFIG
            or name.startswith("test_") or name.endswith("_test.py") or name.split(".")[0] in {"test", "tests", "spec"}
            or bool(re.search(r"(?:\.test\.|\.spec\.|_test\.)", name))
            or name in _LOCK_NAMES or name.endswith(".lock")
            or (name.startswith("requirements") and name.endswith((".txt", ".lock")))
            or any(fnmatch.fnmatchcase(value, pattern) for pattern in extra))


def _link(info: os.stat_result) -> bool:
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                           & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)))


def _checked_root(value: Path, *, exists: bool) -> Path:
    path = Path(os.path.abspath(value))
    for component in [*reversed(path.parents), path]:
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if _link(info):
            raise ValueError("Source/output path cannot contain a link or junction")
    if exists and (not path.is_dir() or not path.exists()):
        raise ValueError("Source must be an existing local repository directory")
    return path


def _read(path: Path, limit: int) -> bytes:
    _checked_root(path.parent, exists=True)
    before = path.lstat()
    if _link(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
        raise ValueError("Repository files cannot be links or special files")
    if before.st_size > limit:
        raise ValueError("Source file exceeds the preparation size limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if _link(opened) or opened.st_nlink > 1 or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Source file changed or became a link during preparation")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    fingerprint = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(raw) > limit or fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(final):
        raise ValueError("Source file changed or exceeded its size limit while being read")
    return raw


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _tree_digest(files: dict[str, dict]) -> str:
    return _digest(json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _safe_env_template(raw: bytes) -> bool:
    """Retain ordinary templates, omit credentials accidentally saved in them."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:sk-|github_pat_|ghp_)[A-Za-z0-9_-]{16,}", text):
        return False
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.removeprefix("export ").split("=", 1)
        value = value.strip().strip("\"'")
        if re.search(r"://[^/\s:@]+:[^/\s@]+@", value):
            return False
        if not re.search(r"TOKEN|PASSWORD|PASSWD|SECRET|API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|CREDENTIAL", name, flags=re.I):
            continue
        placeholder = (not value or value.lower() in {"example", "dummy", "placeholder", "changeme", "change-me", "replace-me", "xxx", "xxxx"}
                       or re.fullmatch(r"\$\{[A-Za-z_][A-Za-z_0-9]*(?::-[^}]*)?\}|<[^<>]+>", value)
                       or re.fullmatch(r"your[-_](?:api[-_])?(?:key|token|secret|password|value)(?:[-_]here)?", value, flags=re.I))
        if not placeholder:
            return False
    return True


def _scan(source: Path, limits: PreparationLimits) -> tuple[dict[str, dict], list[str]]:
    files, excluded = {}, []
    total = 0
    insensitive: set[str] = set()
    directories = [source]
    while directories:
        parent = directories.pop()
        _checked_root(parent, exists=True)
        with os.scandir(parent) as stream:
            entries = sorted(stream, key=lambda entry: entry.name)
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(source).as_posix()
            is_dir = entry.is_dir(follow_symlinks=False)
            if _excluded(relative, directory=is_dir):
                excluded.append(relative)
                continue
            _relative(relative)
            info = entry.stat(follow_symlinks=False)
            if _link(info):
                raise ValueError("Repository cannot contain a link or junction")
            canonical = relative.casefold()
            if canonical in insensitive:
                raise ValueError("Repository path has a case-insensitive collision")
            insensitive.add(canonical)
            if stat.S_ISDIR(info.st_mode):
                directories.append(path)
            elif stat.S_ISREG(info.st_mode):
                if len(files) >= limits.max_files or total + info.st_size > limits.max_total_bytes:
                    raise ValueError("Repository exceeds the preparation file/byte limit")
                raw = _read(path, limits.max_file_bytes)
                if path.name.lower() in _ENV_TEMPLATES and not _safe_env_template(raw):
                    excluded.append(relative)
                    continue
                total += len(raw)
                files[relative] = {"sha256": _digest(raw), "bytes": len(raw), "mode": stat.S_IMODE(info.st_mode) & 0o777}
            else:
                raise ValueError("Repository contains an unsupported special file")
    if not files:
        raise ValueError("Repository has no eligible source files")
    return dict(sorted(files.items())), sorted(excluded)


def _validate(spec: dict) -> dict:
    _fields(spec, _SPEC_REQUIRED, _SPEC_OPTIONAL, "Dataset specification")
    if spec["schema"] != SCHEMA:
        raise ValueError("Unsupported dataset specification schema")
    if not isinstance(spec["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", spec["id"]):
        raise ValueError("Dataset id must be a bounded portable identifier")
    if not isinstance(spec["track"], str) or not isinstance(spec["domain"], str) or spec["track"] not in TRACKS or spec["domain"] not in DOMAINS:
        raise ValueError("Unsupported dataset track/domain")
    _text(spec.get("title", spec["id"]), "title", 160)
    source = _fields(spec["source"], {"repository", "commit", "license"}, set(), "source")
    if not isinstance(source["repository"], str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["repository"]):
        raise ValueError("source.repository must be an explicit GitHub repository URL")
    if not isinstance(source["commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise ValueError("source.commit must be a declared full lowercase 40-hex revision")
    _text(source["license"], "source.license", 160)
    requirements = spec["requirements"]
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 64:
        raise ValueError("requirements must contain 1–64 explicit requirements")
    ids: set[str] = set()
    for requirement in requirements:
        _fields(requirement, {"id", "description", "expected"}, set(), "requirement")
        identifier = _text(requirement["id"], "requirement.id", 96)
        if identifier in ids:
            raise ValueError("Duplicate requirement id")
        ids.add(identifier)
        _text(requirement["description"], "requirement.description", 4000)
        _text(requirement["expected"], "requirement.expected", 4000)
    extra = spec.get("protected_paths", [])
    if not isinstance(extra, list) or len(extra) > 128:
        raise ValueError("protected_paths must be a bounded list")
    for pattern in extra:
        _relative(pattern, pattern=True)
    allowed = spec["allowed_changes"]
    if (not isinstance(allowed, list) or not 1 <= len(allowed) <= 4096 or not all(isinstance(name, str) for name in allowed)
            or len({name.casefold() for name in allowed}) != len(allowed)):
        raise ValueError("allowed_changes must be a unique bounded list of paths")
    for value in allowed:
        _relative(value)
        if _protected(value, extra) or _excluded(value):
            raise ValueError("Patch path is protected: " + value)
    mutations = spec.get("mutations", [])
    if not isinstance(mutations, list) or len(mutations) > 32:
        raise ValueError("mutations must be a bounded list")
    if spec["track"] == "requirement_implementation" and mutations:
        raise ValueError("requirement_implementation cannot include mutations")
    if spec["track"] == "controlled_defect_repair" and not mutations:
        raise ValueError("controlled_defect_repair requires an explicit mutation")
    seen: set[str] = set()
    for mutation in mutations:
        _fields(mutation, {"kind", "path", "expected_sha256"}, {"old", "new"}, "mutation")
        name = _relative(mutation["path"])
        if name not in allowed or name in seen:
            raise ValueError("Mutation path must be unique and in allowed_changes")
        seen.add(name)
        if not isinstance(mutation["expected_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", mutation["expected_sha256"]):
            raise ValueError("Mutation requires a full original file hash")
        if mutation["kind"] == "replace_text":
            if (set(mutation) != {"kind", "path", "expected_sha256", "old", "new"}
                    or not isinstance(mutation["old"], str) or not mutation["old"]
                    or not isinstance(mutation["new"], str) or mutation["old"] == mutation["new"]
                    or max(len(mutation["old"]), len(mutation["new"])) > 500_000):
                raise ValueError("replace_text requires bounded distinct old/new text fields")
        elif mutation["kind"] == "remove_file":
            if set(mutation) != {"kind", "path", "expected_sha256"}:
                raise ValueError("remove_file does not accept replacement fields")
        else:
            raise ValueError("Unsupported mutation kind")
    # Freeze a UTF-8 JSON-only value before any writes; malformed surrogate
    # escapes must not fail only after complete repository copies were made.
    try:
        frozen = json.loads(json.dumps(spec, ensure_ascii=False, allow_nan=False))
        _json_bytes(frozen)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("Dataset specification must contain valid UTF-8 JSON values") from None
    return frozen


def _changed_sources(spec: dict, source: Path, files: dict[str, dict], limits: PreparationLimits) -> dict[str, bytes | None]:
    changes: dict[str, bytes | None] = {}
    for mutation in spec.get("mutations", []):
        name = mutation["path"]
        if name not in files or files[name]["sha256"] != mutation["expected_sha256"]:
            raise ValueError("Mutation original file hash did not match the selected source")
        raw = _read(source / name, limits.max_file_bytes)
        if _digest(raw) != mutation["expected_sha256"]:
            raise ValueError("Mutation source hash changed during preparation")
        if mutation["kind"] == "remove_file":
            changes[name] = None
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("replace_text requires an original UTF-8 source file") from None
        if text.count(mutation["old"]) != 1:
            raise ValueError("replace_text must match exactly once in the sealed original file")
        changed = text.replace(mutation["old"], mutation["new"], 1).encode("utf-8")
        if len(changed) > limits.max_file_bytes:
            raise ValueError("Injected source exceeds the preparation file size limit")
        changes[name] = changed
    return changes


def _write_exclusive(path: Path, raw: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(raw)
    if mode is not None:
        path.chmod(stat.S_IMODE(mode) & 0o777)


def prepare_dataset_case(spec: dict, source: Path, output: Path, *, limits: PreparationLimits | None = None) -> dict:
    """Prepare isolated pristine, broken/input, and initial candidate trees.

    Every deterministic validation precedes the first write. The source is
    always read-only; an existing output is rejected, including empty folders.
    No health, build, test, model, simulator or device execution is performed.
    """
    limits = limits or PreparationLimits()
    checked = _validate(spec)
    source = _checked_root(Path(source), exists=True)
    output = _checked_root(Path(output), exists=False)
    if source == output or source.is_relative_to(output) or output.is_relative_to(source):
        raise ValueError("Source and output paths cannot overlap")
    if output.exists():
        raise ValueError("Output already exists; prior evidence must never be overwritten")
    files, excluded = _scan(source, limits)
    changes = _changed_sources(checked, source, files, limits)
    canonical = {}
    for name in files:
        for path in [PurePosixPath(name), *PurePosixPath(name).parents]:
            canonical[str(path).casefold()] = str(path)
    for name in checked["allowed_changes"]:
        for path in [PurePosixPath(name), *PurePosixPath(name).parents]:
            previous = canonical.get(str(path).casefold())
            if previous is not None and previous != str(path):
                raise ValueError("Allowed path spelling conflicts with the actual source path")
        # A feature may create a file, but must not replace an existing directory.
        target = source / name
        if target.exists() and not target.is_file():
            raise ValueError("Allowed patch path refers to a directory")
        for parent in target.parents:
            if parent == source:
                break
            if parent.is_file():
                raise ValueError("Allowed patch path traverses an existing file")
    modified = {name: value.copy() for name, value in files.items()}
    for name, raw in changes.items():
        if raw is None:
            modified.pop(name)
        else:
            modified[name] = {**files[name], "sha256": _digest(raw), "bytes": len(raw)}
    if not modified:
        raise ValueError("Injected source cannot remove the entire project snapshot")
    if sum(value["bytes"] for value in modified.values()) > limits.max_total_bytes:
        raise ValueError("Injected repository exceeds the preparation total byte limit")
    # mkdir(exist_ok=False) reserves a fresh case; no cleanup/delete can touch a
    # pre-existing directory. Unexpected I/O failures leave evidence for review.
    output.mkdir(parents=True, exist_ok=False)
    roots = {"pristine": output / "evaluator-only/pristine", "agent_input": output / "agent-input/project",
             "initial_candidate": output / "candidate"}
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=False)
    for name, expected in files.items():
        raw = _read(source / name, limits.max_file_bytes)
        if _digest(raw) != expected["sha256"]:
            raise ValueError("Source hash changed after its preparation seal")
        mode = expected["mode"]
        _write_exclusive(roots["pristine"] / name, raw, mode)
        broken = changes.get(name, raw)
        if broken is not None:
            for key in ("agent_input", "initial_candidate"):
                _write_exclusive(roots[key] / name, broken, mode)
    # Re-scan to reject additions/removals or mutations made during copying.
    final_files, _ = _scan(source, limits)
    if final_files != files:
        raise ValueError("Source tree changed during preparation; output is not published")
    for key, root in roots.items():
        actual, _ = _scan(root, limits)
        if actual != (files if key == "pristine" else modified):
            raise ValueError("Prepared snapshot differs from its sealed source inventory")
    digest = _tree_digest(modified)
    public = {
        "schema": "testfix.dataset-agent-input.v1", "id": checked["id"], "title": checked.get("title", checked["id"]),
        "track": checked["track"], "domain": checked["domain"],
        "source": {**checked["source"], "revision_verification": "declared_not_checked"},
        "requirements": checked["requirements"], "allowed_changes": checked["allowed_changes"],
        "project_sha256": digest, "status": "preflight_pending", "preparation_only": True,
    }
    private = {
        "schema": "testfix.dataset-preparation-private.v1", "id": checked["id"],
        "mutations": checked.get("mutations", []), "protected_paths": checked.get("protected_paths", []),
        "pristine_health": "unverified", "pristine_files": files, "agent_input_files": modified,
        "excluded_paths": excluded, "excluded_paths_review_required": bool(excluded),
    }
    snapshots = {}
    for key, root in roots.items():
        inventory = files if key == "pristine" else modified
        snapshots[key] = {"root": root.relative_to(output).as_posix(), "sha256": _tree_digest(inventory),
                          "file_count": len(inventory), "bytes": sum(value["bytes"] for value in inventory.values())}
    manifest = {
        "schema": "testfix.dataset-case-prepared.v1", "id": checked["id"], "track": checked["track"], "domain": checked["domain"],
        "source": {**checked["source"], "revision_verification": "declared_not_checked"},
        "status": "preflight_pending", "preparation_only": True,
        "preflight": {"status": "pending", "executed": False}, "snapshots": snapshots,
        "source_copy": "filtered_snapshot_not_verified_build",
        "review_required": ["verify_operator_supplied_upstream_revision", "establish_environment_and_independent_contract",
                            *(["review_excluded_source_paths"] if excluded else [])],
        "isolation": "directory_layout_only_not_an_operating_system_sandbox",
        "public_task": "agent-input/task.json", "public_task_sha256": _digest(_json_bytes(public)),
        "private_preparation": "evaluator-only/preparation.json", "private_preparation_sha256": _digest(_json_bytes(private)),
    }
    _write_exclusive(output / "agent-input/task.json", _json_bytes(public))
    _write_exclusive(output / "evaluator-only/preparation.json", _json_bytes(private))
    # Presence of this manifest is the publication boundary; incomplete output
    # has no manifest and must not be registered as a prepared case.
    _write_exclusive(output / "manifest.json", _json_bytes(manifest))
    return manifest
