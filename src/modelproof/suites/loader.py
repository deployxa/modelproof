"""Suite and task validation (spec §5, §13 "Input and suite validation").

A suite directory layout:

    suites/python-core/
      suite.yaml                      # manifest: id, version, task list, provenance
      tasks/<task_id>/task.yaml       # per-task metadata (timeout, limits, provenance)
      tasks/<task_id>/instruction.md  # model-facing instructions
      tasks/<task_id>/starter.py      # starter code (model-facing)
      tasks/<task_id>/solution.py     # reference solution (NEVER sent to the model)
      tests_private/<task_id>/test_checks.py   # private acceptance checks
      harness/run_checks.py           # sandbox-side harness (public)
      fixtures/recorded_outcomes.json # dev-side validation records (fixture runs)

Validation rejects: duplicate IDs, malformed manifests, missing files, path
traversal, symlink escapes, corrupt digests, duplicate content, and private
test leakage into model-facing payloads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..errors import InvalidInputError
from ..hashing import digest_obj, normalize_text, safe_relative_path, sha256_bytes, sha256_text
from ..schemas import SuiteManifest, TaskSpec
from .leakage import LeakageReport, check_leakage

REQUIRED_TASK_FILES = ("task.yaml", "instruction.md", "starter.py", "solution.py")


@dataclass
class LoadedTask:
    spec: TaskSpec
    dir: Path
    instruction_path: Path
    starter_path: Path
    solution_path: Path
    checks_path: Path
    content_digest: str = ""

    @property
    def instruction(self) -> str:
        return self.instruction_path.read_text(encoding="utf-8")

    @property
    def starter(self) -> str:
        return self.starter_path.read_text(encoding="utf-8")

    @property
    def checks(self) -> str:
        return self.checks_path.read_text(encoding="utf-8")

    @property
    def has_canary(self) -> bool:
        from .canary import has_canary

        return has_canary(self.instruction)


@dataclass
class LoadedSuite:
    manifest: SuiteManifest
    root: Path
    tasks: list[LoadedTask] = field(default_factory=list)
    content_hash: str = ""
    leakage: LeakageReport | None = None

    def task(self, task_id: str) -> LoadedTask:
        for t in self.tasks:
            if t.spec.id == task_id:
                return t
        raise InvalidInputError(f"task {task_id!r} not in suite {self.manifest.suite_id!r}")


def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        raise InvalidInputError(f"missing file: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise InvalidInputError(f"malformed YAML in {path.name}: {e}") from e
    if not isinstance(data, dict):
        raise InvalidInputError(f"{path.name} must be a YAML mapping")
    return data


def _reject_symlink_escapes(root: Path) -> None:
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            target = path.resolve()
            if target != root and root not in target.parents:
                raise InvalidInputError(f"symlink escapes suite root: {path} -> {target}")


def load_suite(root: Path) -> LoadedSuite:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise InvalidInputError(f"suite directory not found: {root}")
    _reject_symlink_escapes(root)

    data = _read_yaml(root / "suite.yaml")
    try:
        manifest = SuiteManifest.model_validate(data)
    except Exception as e:
        raise InvalidInputError(f"invalid suite.yaml: {e}") from e

    ids = [t.id for t in manifest.tasks]
    if len(ids) != len(set(ids)):
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        raise InvalidInputError(f"duplicate task IDs in suite.yaml: {dupes}")

    listed = set(ids)
    on_disk = (
        {p.name for p in (root / "tasks").glob("*") if p.is_dir()}
        if (root / "tasks").is_dir()
        else set()
    )
    if listed != on_disk:
        missing = sorted(listed - on_disk)
        extra = sorted(on_disk - listed)
        problems = []
        if missing:
            problems.append(f"listed in suite.yaml but missing on disk: {missing}")
        if extra:
            problems.append(f"on disk but not in suite.yaml: {extra}")
        raise InvalidInputError("; ".join(problems))

    tasks: list[LoadedTask] = []
    for entry in manifest.tasks:
        tdir = safe_relative_path(root, f"tasks/{entry.id}")
        for fname in REQUIRED_TASK_FILES:
            p = tdir / fname
            if not p.is_file() or p.is_symlink():
                raise InvalidInputError(f"task {entry.id!r}: missing required file {fname}")
        checks_path = safe_relative_path(root, f"tests_private/{entry.id}/test_checks.py")
        if not checks_path.is_file() or checks_path.is_symlink():
            raise InvalidInputError(f"task {entry.id!r}: missing private checks test_checks.py")

        meta = _read_yaml(tdir / "task.yaml")
        if meta.get("id") != entry.id:
            raise InvalidInputError(
                f"task {entry.id!r}: task.yaml id mismatch ({meta.get('id')!r})"
            )
        if meta.get("category") and meta["category"] != entry.category.value:
            raise InvalidInputError(
                f"task {entry.id!r}: category mismatch between task.yaml and suite.yaml"
            )
        try:
            spec = TaskSpec(
                id=entry.id,
                title=entry.title,
                category=entry.category,
                instruction=(tdir / "instruction.md").read_text(encoding="utf-8"),
                timeout_seconds=int(meta.get("timeout_seconds", 10)),
                memory_limit_mb=int(meta.get("memory_limit_mb", 256)),
                scoring_rule=str(meta.get("scoring_rule", "all_private_checks_must_pass")),
                provenance=meta.get("provenance") or {},
            )
        except Exception as e:
            raise InvalidInputError(f"task {entry.id!r}: invalid metadata: {e}") from e

        lt = LoadedTask(
            spec=spec,
            dir=tdir,
            instruction_path=tdir / "instruction.md",
            starter_path=tdir / "starter.py",
            solution_path=tdir / "solution.py",
            checks_path=checks_path,
        )
        lt.content_digest = digest_obj(
            {
                "id": spec.id,
                "instruction": sha256_text(spec.instruction),
                "starter": sha256_bytes(lt.starter_path.read_bytes()),
                "solution": sha256_bytes(lt.solution_path.read_bytes()),
                "checks": sha256_bytes(checks_path.read_bytes()),
                "meta": sha256_bytes((tdir / "task.yaml").read_bytes()),
            }
        )
        tasks.append(lt)

    suite_hash = digest_obj(
        {
            "suite_id": manifest.suite_id,
            "version": manifest.version,
            "tasks": [{"id": t.spec.id, "digest": t.content_digest} for t in tasks],
        }
    )
    manifest.content_hash = suite_hash

    leakage = check_leakage(tasks)
    if leakage.exact_dupes or leakage.normalized_dupes or leakage.private_test_leaks:
        raise InvalidInputError(f"leakage/duplicate checks failed: {leakage.summary()}")

    suite = LoadedSuite(
        manifest=manifest, root=root, tasks=tasks, content_hash=suite_hash, leakage=leakage
    )
    return suite


def register_suite(db, suite: LoadedSuite, source_path: Path) -> None:
    m = suite.manifest
    db.execute(
        "INSERT INTO suites(suite_id, version, path, content_hash, manifest_json) VALUES (?,?,?,?,?) "
        "ON CONFLICT(suite_id, version) DO UPDATE SET path=excluded.path,"
        " content_hash=excluded.content_hash, manifest_json=excluded.manifest_json",
        (
            m.suite_id,
            m.version,
            str(Path(source_path).resolve()),
            suite.content_hash,
            m.model_dump_json(),
        ),
    )
    db.commit()


def normalized_task_fingerprint(instruction_text: str) -> str:
    return sha256_text(normalize_text(instruction_text))
