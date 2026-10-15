"""Load versioned Skill instructions and validate declared workflow requests.

This module checks logical resource names and workflow metadata. It neither
executes Skills nor provides an operating-system sandbox or delivery evidence.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping


CATALOG_SCHEMA = "testfix.skill-catalog.v1"
CONTRACT_SCHEMA = "testfix.skill.v1"
SNAPSHOT_SCHEMA = "testfix.skill-snapshot.v1"
DEFAULT_CATALOG = Path(__file__).parent / "skill_library" / "catalog.json"
LEGACY_PROFILES = frozenset({"pure_python", "flaskr_project"})
PROFILES = LEGACY_PROFILES | {"python_package"}
ROLES = frozenset({"requirements", "tester", "diagnosis", "developer", "reviewer"})
PROTECTED_RESOURCES = frozenset({"independent_acceptance", "regression_tests", "reference_solution"})
HIDDEN_RESOURCES = frozenset({"independent_acceptance", "reference_solution"})
MINIMUM_GATES = {
    "pure_python": frozenset({"generated_tests", "independent_acceptance", "regression_preservation", "candidate_policy", "review"}),
    "flaskr_project": frozenset({"paired_test_ids", "source_binding", "original_regression", "generated_tests",
                                 "independent_acceptance", "protected_hashes", "review_hash", "wheel_build",
                                 "real_http", "sqlite_restart"}),
    "python_package": frozenset({"paired_test_ids", "source_binding", "original_regression", "generated_tests",
                                 "independent_acceptance", "protected_hashes", "review_hash", "wheel_build",
                                 "real_cli"}),
}
_PURE_CORE = ("analyze-requirements", "generate-tests", "diagnose-results", "repair-code", "review-patch", "verify-delivery")
MINIMUM_SKILLS = {
    "pure_python": _PURE_CORE,
    "flaskr_project": ("plan-workflow", "explore-repository", "ground-code") + _PURE_CORE,
    "python_package": ("plan-workflow", "explore-repository", "ground-code") + _PURE_CORE,
}
_ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


class SkillValidationError(ValueError):
    """A closed-by-default catalog or invocation validation failure."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def _fail(code: str, message: str) -> None:
    raise SkillValidationError(code, message)


def _object(value: Any, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        _fail("invalid_schema", f"{label} must contain exactly {sorted(keys)}")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail("invalid_schema", f"{label} must be a nonempty, trimmed string")
    return value


def _strings(value: Any, label: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list):
        _fail("invalid_schema", f"{label} must be a list of strings")
    result = [_string(item, label) for item in value]
    if len(result) != len(set(result)) or (nonempty and not result):
        _fail("invalid_schema", f"{label} must contain unique values" + (" and not be empty" if nonempty else ""))
    return result


def _positive_integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 1:
        _fail("invalid_schema", f"{label} must be an integer of at least 1")
    return value


def _json(raw: bytes, label: str) -> dict:
    def unique_keys(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("invalid_schema", f"{label} contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        _fail("invalid_schema", f"{label} is not UTF-8 JSON: {error}")
    if not isinstance(value, dict):
        _fail("invalid_schema", f"{label} must be a JSON object")
    return value


class SkillCatalog:
    """An immutable-in-practice snapshot of a validated local Skill library.

    Construct with an explicit catalog file or use the bundled library. Public
    metadata accessors return copies so callers cannot loosen validated scopes.
    """

    def __init__(self, catalog_path: str | Path | None = None):
        requested = Path(catalog_path) if catalog_path is not None else DEFAULT_CATALOG
        self.root = requested.parent.resolve()
        self.path = requested.absolute()
        self._files: dict[str, bytes] = {}
        self._contracts: dict[str, dict] = {}
        self._bodies: dict[str, str] = {}
        raw = self._read("catalog.json", requested)
        self._catalog = self._validate_catalog(_json(raw, "catalog"))
        for entry in self._catalog["skills"]:
            skill_id = entry["id"]
            body_raw = self._read(entry["path"])
            try:
                body = body_raw.decode("utf-8")
            except UnicodeDecodeError:
                _fail("invalid_schema", f"{skill_id} SKILL.md must be UTF-8")
            if not body.strip():
                _fail("invalid_schema", f"{skill_id} SKILL.md must not be empty")
            self._bodies[skill_id] = body
            contract = _json(self._read(entry["contract"]), f"{skill_id} contract")
            self._contracts[skill_id] = self._validate_contract(skill_id, contract)
        self._validate_relations()
        self._snapshot = {
            "schema_version": SNAPSHOT_SCHEMA,
            "catalog_version": self._catalog["version"],
            "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(self._files.items())},
        }

    @classmethod
    def load(cls, catalog_path: str | Path | None = None) -> SkillCatalog:
        return cls(catalog_path)

    def _read(self, name: str, explicit_path: Path | None = None) -> bytes:
        path = explicit_path if explicit_path is not None else self.root / name
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(self.root):
                _fail("unsafe_path", f"{name} resolves outside the catalog directory")
            if not resolved.is_file():
                _fail("missing_file", f"{name} is not a regular file")
            raw = resolved.read_bytes()
        except (OSError, RuntimeError) as error:
            _fail("missing_file", f"Cannot read {name}: {error}")
        self._files[name] = raw
        return raw

    def _validate_catalog(self, catalog: dict) -> dict:
        _object(catalog, {"schema_version", "version", "skills", "roles", "profiles"}, "catalog")
        if catalog["schema_version"] != CATALOG_SCHEMA:
            _fail("unknown_schema", "Unsupported catalog schema_version")
        _string(catalog["version"], "catalog.version")
        if not isinstance(catalog["skills"], list) or not catalog["skills"]:
            _fail("invalid_schema", "catalog.skills must be a nonempty list")
        seen = set()
        for entry in catalog["skills"]:
            _object(entry, {"id", "path", "contract"}, "catalog skill entry")
            skill_id = _string(entry["id"], "skill.id")
            if not _ID.fullmatch(skill_id):
                _fail("unsafe_path", f"Invalid Skill id {skill_id!r}")
            if skill_id in seen:
                _fail("duplicate_id", f"Duplicate Skill id {skill_id}")
            seen.add(skill_id)
            if entry["path"] != f"{skill_id}/SKILL.md" or entry["contract"] != f"{skill_id}/references/contract.json":
                _fail("unsafe_path", f"{skill_id} paths must be bound to its own directory")
        _object(catalog["roles"], set(ROLES), "catalog.roles")
        for role, skill_id in catalog["roles"].items():
            _string(skill_id, f"roles.{role}")
            if skill_id not in seen:
                _fail("unknown_skill", f"Role {role} references unknown Skill {skill_id}")
        if len(set(catalog["roles"].values())) != len(ROLES):
            _fail("invalid_schema", "Each existing role must map to a distinct Skill")
        # v1/v1.1 snapshots remain valid with their original two profiles. The
        # v1.2 catalog must explicitly declare the package/CLI delivery scope.
        expected_profiles = PROFILES if catalog["version"] == "1.2.0" else LEGACY_PROFILES
        _object(catalog["profiles"], set(expected_profiles), "catalog.profiles")
        for profile, definition in catalog["profiles"].items():
            _object(definition, {"required_skills", "protected_resources", "required_gates"}, f"profile {profile}")
            required = _strings(definition["required_skills"], f"{profile}.required_skills", nonempty=True)
            _strings(definition["protected_resources"], f"{profile}.protected_resources")
            gates = _strings(definition["required_gates"], f"{profile}.required_gates", nonempty=True)
            missing_gates = MINIMUM_GATES[profile] - set(gates)
            if missing_gates:
                _fail("missing_required_gate", f"{profile} must require delivery gates {sorted(missing_gates)}")
            if not set(required) <= seen:
                _fail("unknown_skill", f"{profile} requires unknown Skills: {sorted(set(required) - seen)}")
        return catalog

    def _validate_contract(self, skill_id: str, contract: dict) -> dict:
        _object(contract, {"schema_version", "id", "version", "kind", "role", "paper_capability", "profiles",
                           "inputs", "outputs", "reads", "writes", "constraints", "prerequisites", "budgets",
                           "failure", "optimization"}, f"{skill_id} contract")
        if contract["schema_version"] != CONTRACT_SCHEMA:
            _fail("unknown_schema", f"Unsupported contract schema for {skill_id}")
        if contract["id"] != skill_id:
            _fail("id_mismatch", f"Contract id must match {skill_id}")
        if contract["version"] not in {"1.0.0", "1.1.0", "1.2.0"}:
            _fail("unknown_schema", f"Unsupported contract version for {skill_id}")
        if not isinstance(contract["kind"], str) or contract["kind"] not in {"llm", "deterministic"}:
            _fail("invalid_schema", f"{skill_id}.kind must be llm or deterministic")
        for key in ("role", "paper_capability"):
            _string(contract[key], f"{skill_id}.{key}")
        profiles = _strings(contract["profiles"], f"{skill_id}.profiles", nonempty=True)
        if not set(profiles) <= set(self._catalog["profiles"]):
            _fail("unknown_profile", f"{skill_id} declares unknown profiles")
        for key in ("inputs", "outputs", "reads", "writes", "prerequisites"):
            _strings(contract[key], f"{skill_id}.{key}")
        self._check_reads(skill_id, contract["kind"], contract["reads"])
        self._check_writes(skill_id, contract["writes"], profiles)
        if not isinstance(contract["constraints"], list):
            _fail("invalid_schema", f"{skill_id}.constraints must be a list")
        constraint_ids = set()
        for constraint in contract["constraints"]:
            _object(constraint, {"id", "description", "enforcement", "check"}, f"{skill_id} constraint")
            for key in ("id", "description"):
                _string(constraint[key], f"{skill_id}.constraint.{key}")
            if constraint["id"] in constraint_ids:
                _fail("invalid_schema", f"{skill_id} repeats a constraint id")
            constraint_ids.add(constraint["id"])
            if not isinstance(constraint["enforcement"], str) or constraint["enforcement"] not in {"prompt_only", "existing_runtime", "catalog_validator"}:
                _fail("invalid_schema", f"{skill_id} has unknown constraint enforcement")
            if constraint["check"] is not None:
                _string(constraint["check"], f"{skill_id}.constraint.check")
        budgets = _object(contract["budgets"], {"max_attempts", "timeout_seconds"}, f"{skill_id}.budgets")
        for key, value in budgets.items():
            _positive_integer(value, f"{skill_id}.budgets.{key}")
        failure = _object(contract["failure"], {"codes", "feedback_fields"}, f"{skill_id}.failure")
        for key, value in failure.items():
            _strings(value, f"{skill_id}.failure.{key}")
        optimization = _object(contract["optimization"], {"mandatory", "resources", "estimated_seconds", "estimated_tokens"},
                               f"{skill_id}.optimization")
        if type(optimization["mandatory"]) is not bool:
            _fail("invalid_schema", f"{skill_id}.optimization.mandatory must be boolean")
        _strings(optimization["resources"], f"{skill_id}.optimization.resources")
        if optimization["estimated_seconds"] is not None or optimization["estimated_tokens"] is not None:
            _fail("invalid_schema", f"{skill_id} estimates must be null until measured")
        return contract

    def _check_reads(self, skill_id: str, kind: str, reads: list[str]) -> None:
        forbidden = set(reads) & HIDDEN_RESOURCES
        if kind == "llm" and forbidden:
            _fail("protected_read", f"LLM Skill {skill_id} cannot read hidden resources {sorted(forbidden)}")

    def _check_writes(self, skill_id: str, writes: list[str], profiles: list[str]) -> None:
        protected = set(PROTECTED_RESOURCES)
        for profile in profiles:
            protected.update(self._catalog["profiles"][profile]["protected_resources"])
        forbidden = set(writes) & protected
        if forbidden:
            _fail("protected_write", f"{skill_id} cannot write protected resources {sorted(forbidden)}")
        if "candidate_sources" in writes and skill_id != "repair-code":
            _fail("protected_write", "Only repair-code may declare writes to candidate_sources")

    def _validate_relations(self) -> None:
        for role, skill_id in self._catalog["roles"].items():
            contract = self._contracts[skill_id]
            if contract["role"] != role or contract["kind"] != "llm":
                _fail("role_mismatch", f"Role {role} must map to an llm Skill with the same role")
        for skill_id, contract in self._contracts.items():
            for prerequisite in contract["prerequisites"]:
                if prerequisite not in self._contracts:
                    _fail("unknown_skill", f"{skill_id} requires unknown Skill {prerequisite}")
                if not set(contract["profiles"]) <= set(self._contracts[prerequisite]["profiles"]):
                    _fail("profile_mismatch", f"{skill_id} prerequisite {prerequisite} is unavailable in one of its profiles")
        visiting, visited = set(), set()

        def visit(skill_id: str) -> None:
            if skill_id in visiting:
                _fail("dependency_cycle", f"Skill dependency cycle includes {skill_id}")
            if skill_id in visited:
                return
            visiting.add(skill_id)
            for prerequisite in self._contracts[skill_id]["prerequisites"]:
                visit(prerequisite)
            visiting.remove(skill_id)
            visited.add(skill_id)

        for skill_id in self._contracts:
            visit(skill_id)
        for profile, definition in self._catalog["profiles"].items():
            for skill_id in definition["required_skills"]:
                self._skill(skill_id, profile)
            required = set(definition["required_skills"])
            if not {"review-patch", "verify-delivery"} <= required:
                _fail("missing_delivery_skill", f"{profile} must require review-patch and verify-delivery")
            core = MINIMUM_SKILLS[profile]
            missing_core = set(core) - required
            if missing_core:
                _fail("missing_core_skill", f"{profile} cannot omit v1 core Skills {sorted(missing_core)}")
            if tuple(skill_id for skill_id in definition["required_skills"] if skill_id in core) != core:
                _fail("invalid_order", f"{profile} core Skills must retain the v1 single-round order")

    @property
    def catalog(self) -> dict:
        return deepcopy(self._catalog)

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(self._contracts)

    def get_contract(self, skill_id: str) -> dict:
        return deepcopy(self._skill(skill_id))

    def _profile(self, profile: str) -> dict:
        if not isinstance(profile, str) or profile not in self._catalog["profiles"]:
            _fail("unknown_profile", f"Unknown Skill profile {profile!r}")
        return self._catalog["profiles"][profile]

    def _skill(self, skill_id: str, profile: str | None = None) -> dict:
        if not isinstance(skill_id, str) or skill_id not in self._contracts:
            _fail("unknown_skill", f"Unknown Skill {skill_id!r}")
        contract = self._contracts[skill_id]
        if profile is not None:
            self._profile(profile)
            if profile not in contract["profiles"]:
                _fail("profile_mismatch", f"{skill_id} is unavailable in {profile}")
        return contract

    def render_instructions(self, role: str, profile: str = "pure_python") -> str:
        definition = self._profile(profile)
        if not isinstance(role, str) or role not in self._catalog["roles"]:
            _fail("unknown_role", f"Unknown agent role {role!r}")
        skill_id = self._catalog["roles"][role]
        contract = self._skill(skill_id, profile)
        protected = sorted(PROTECTED_RESOURCES | set(definition["protected_resources"]))
        return (
            "# Shared TestFix Skill scope\n"
            f"Profile: {profile}\n"
            f"Required workflow Skills: {', '.join(definition['required_skills'])}\n"
            f"Protected resources (never writable): {', '.join(protected)}\n"
            f"Required delivery gates: {', '.join(definition['required_gates'])}\n"
            "Gate labels describe required execution evidence; loading or validating a Skill does not pass a gate.\n"
            "Only repair-code may write candidate_sources. Resource scopes are logical declarations, not an OS sandbox.\n"
            f"This Skill may read: {', '.join(contract['reads']) or '(none)'}\n"
            f"This Skill may write: {', '.join(contract['writes']) or '(none)'}\n"
            "\n" + self._bodies[skill_id]
            + "\n\n# Machine-readable Skill contract\n"
            "The following contract belongs to this role. Its limits, dependencies and resource permissions are declarations; "
            "neither this contract nor a validated plan proves execution or delivery success. "
            "Apply only the selected profile above.\n"
            "```json\n" + json.dumps(contract, ensure_ascii=False, indent=2) + "\n```\n"
        )

    def snapshot(self) -> dict:
        """Return hashes of the exact bytes loaded, rather than current disk data."""
        return deepcopy(self._snapshot)

    def verify_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Reject a different snapshot or any changed/removed/escaped loaded file."""
        if not isinstance(snapshot, Mapping) or dict(snapshot) != self._snapshot:
            _fail("stale_snapshot", "Snapshot does not match this loaded Skill catalog")
        for name, expected in self._snapshot["files"].items():
            path = self.path if name == "catalog.json" else self.root / name
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(self.root) or not resolved.is_file():
                    _fail("stale_snapshot", f"Loaded file {name} changed its path")
                current = hashlib.sha256(resolved.read_bytes()).hexdigest()
            except (OSError, RuntimeError):
                _fail("stale_snapshot", f"Loaded file {name} is unavailable")
            if current != expected:
                _fail("stale_snapshot", f"Loaded file {name} has changed")

    def validate_invocation(self, skill_id: str, profile: str, reads: list[str], writes: list[str],
                            completed: list[str], attempt: int, timeout_seconds: int) -> dict:
        """Validate a request; completed IDs are caller-supplied, not proof of execution."""
        contract = self._skill(skill_id, profile)
        requested_reads = _strings(reads, "invocation.reads")
        requested_writes = _strings(writes, "invocation.writes")
        finished = _strings(completed, "invocation.completed")
        for prerequisite in finished:
            self._skill(prerequisite, profile)
        self._check_reads(skill_id, contract["kind"], requested_reads)
        self._check_writes(skill_id, requested_writes, [profile])
        if not set(requested_reads) <= set(contract["reads"]) or not set(requested_writes) <= set(contract["writes"]):
            _fail("scope_violation", f"{skill_id} requested undeclared resources")
        missing = set(contract["prerequisites"]) - set(finished)
        if missing:
            _fail("missing_prerequisite", f"{skill_id} requires completed Skills {sorted(missing)}")
        for name, value in (("attempt", attempt), ("timeout_seconds", timeout_seconds)):
            _positive_integer(value, f"invocation.{name}")
        if attempt > contract["budgets"]["max_attempts"] or timeout_seconds > contract["budgets"]["timeout_seconds"]:
            _fail("budget_exceeded", f"{skill_id} exceeds its attempt or timeout budget")
        return {"skill_id": skill_id, "profile": profile, "reads": requested_reads.copy(), "writes": requested_writes.copy(),
                "completed": finished.copy(), "attempt": attempt, "timeout_seconds": timeout_seconds,
                "validation": "declared_request_only", "execution_verified": False}

    def validate_plan(self, profile: str, skill_ids: list[str]) -> dict:
        """Check the declared order and required Skills, without executing the plan."""
        definition = self._profile(profile)
        plan = _strings(skill_ids, "plan.skill_ids", nonempty=True)
        available = set()
        for skill_id in plan:
            contract = self._skill(skill_id, profile)
            missing = set(contract["prerequisites"]) - available
            if missing:
                _fail("missing_prerequisite", f"{skill_id} must follow {sorted(missing)}")
            available.add(skill_id)
        mandatory = set(definition["required_skills"]) | {"review-patch", "verify-delivery"}
        mandatory.update(skill_id for skill_id, contract in self._contracts.items()
                         if profile in contract["profiles"] and contract["optimization"]["mandatory"])
        missing = mandatory - available
        if missing:
            _fail("missing_required_skill", f"{profile} plan is missing required Skills {sorted(missing)}")
        required_order = definition["required_skills"]
        if [skill_id for skill_id in plan if skill_id in required_order] != required_order:
            _fail("invalid_order", f"{profile} required Skills must follow the catalog's declared single-round order")
        return {"profile": profile, "skill_ids": plan.copy(), "required_skills": sorted(mandatory),
                "required_order": required_order.copy(),
                "required_gates": definition["required_gates"].copy(), "gate_status": "execution_evidence_required",
                "validation": "declared_plan_only", "execution_verified": False}


def load_catalog(catalog_path: str | Path | None = None) -> SkillCatalog:
    """Convenience entry point with the same closed-by-default loading behavior."""
    return SkillCatalog(catalog_path)
