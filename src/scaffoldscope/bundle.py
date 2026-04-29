"""Deterministic, workspace-free evidence bundles for sharing experiment results."""

from __future__ import annotations

import hashlib
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import canonical_json, content_hash, load_json, strict_json_loads
from scaffoldscope.report import check_experiment, write_report

_REPORT_FILES = {
    "paired-comparisons.csv",
    "report.html",
    "report.md",
    "summary.csv",
    "summary.json",
}

_ROOT_FILES = {
    "config.resolved.json",
    "episodes.jsonl",
    "manifest.json",
    "plan.jsonl",
    "pricing.json",
} | _REPORT_FILES
_TRIAL_FILES = {"events.jsonl", "patch.diff", "result.json"}
_MANIFEST_NAME = "BUNDLE-MANIFEST.json"
_MAX_ENTRY_BYTES = 100 * 1024 * 1024
_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_archive_name(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or path.as_posix() != name
        or any(part in {"", "."} for part in path.parts)
    ):
        raise ConfigError(f"Unsafe evidence-bundle path: {name!r}")
    return path.as_posix()


def _allowed_evidence_name(name: str) -> bool:
    if name in _ROOT_FILES:
        return True
    parts = PurePosixPath(name).parts
    if len(parts) == 3 and parts[0] in {"trials", "aborted-attempts"}:
        return bool(parts[1]) and parts[2] in _TRIAL_FILES
    return (
        len(parts) == 2
        and parts[0] == "external-evaluations"
        and bool(parts[1])
        and parts[1].endswith(".json")
    )


def _evidence_files(root: Path) -> list[Path]:
    files = [root / name for name in sorted(_ROOT_FILES) if (root / name).is_file()]
    trials = root / "trials"
    if trials.is_dir():
        files.extend(
            path
            for path in sorted(trials.glob("*/*"))
            if path.is_file() and path.name in _TRIAL_FILES
        )
    overlays = root / "external-evaluations"
    if overlays.is_dir():
        files.extend(path for path in sorted(overlays.glob("*.json")) if path.is_file())
    aborted = root / "aborted-attempts"
    if aborted.is_dir():
        files.extend(
            path
            for path in sorted(aborted.glob("*/*"))
            if path.is_file() and path.name in _TRIAL_FILES
        )
    return files


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    return info


def create_evidence_bundle(experiment_dir: Path, output: Path) -> dict[str, Any]:
    """Create a deterministic archive containing evidence but no mutable workspaces."""

    root = experiment_dir.resolve()
    output = output.resolve()
    if output.exists():
        raise ConfigError(f"Refusing to overwrite existing evidence bundle: {output}")
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise ConfigError("Evidence bundle output must be outside the experiment directory")
    valid, issues = check_experiment(root)
    if not valid:
        raise ConfigError("Experiment integrity check failed: " + "; ".join(issues))
    missing_root = sorted(name for name in _ROOT_FILES if not (root / name).is_file())
    if missing_root:
        raise ConfigError(
            "Experiment has no complete publication report; run scaffoldscope report first. "
            f"Missing: {missing_root}"
        )
    # Reports are derived evidence. Rebuild them from the frozen resolved configuration so
    # stale files, manual edits, and one-off CLI analysis overrides cannot enter a bundle.
    write_report(root)
    manifest_source = load_json(root / "manifest.json")
    if not isinstance(manifest_source, dict):
        raise ConfigError("Experiment manifest must be a JSON object")

    payloads: dict[str, bytes] = {}
    for path in _evidence_files(root):
        if path.is_symlink():
            raise ConfigError(f"Evidence files must not be symlinks: {path}")
        resolved_path = path.resolve()
        try:
            contained = resolved_path.relative_to(root)
        except ValueError as exc:
            raise ConfigError(f"Evidence file escapes the experiment: {path}") from exc
        relative = _safe_archive_name(contained.as_posix())
        data = resolved_path.read_bytes()
        if len(data) > _MAX_ENTRY_BYTES:
            raise ConfigError(f"Evidence file exceeds {_MAX_ENTRY_BYTES} bytes: {relative}")
        payloads[relative] = data
    if not payloads:
        raise ConfigError("Experiment contains no evidence files")
    total_bytes = sum(len(value) for value in payloads.values())
    if total_bytes > _MAX_TOTAL_BYTES:
        raise ConfigError("Evidence bundle exceeds the uncompressed size limit")
    files = {
        name: {"sha256": _sha256(data), "bytes": len(data)}
        for name, data in sorted(payloads.items())
    }
    identity = {
        "schema_version": 1,
        "kind": "scaffoldscope-evidence-bundle",
        "experiment": manifest_source.get("experiment"),
        "config_hash": manifest_source.get("config_hash"),
        "files": files,
    }
    bundle_manifest = {**identity, "bundle_hash": content_hash(identity)}
    manifest_bytes = (canonical_json(bundle_manifest) + "\n").encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr(_zip_info(_MANIFEST_NAME), manifest_bytes)
        for name, data in sorted(payloads.items()):
            archive.writestr(_zip_info(name), data)
    return bundle_manifest


def verify_evidence_bundle(path: Path) -> dict[str, Any]:
    """Verify archive structure, hashes, identity, and evidence relationships."""

    archive_path = path.resolve()
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ConfigError(f"Could not open evidence bundle {archive_path}: {exc}") from exc
    with archive:
        infos = archive.infolist()
        for info in infos:
            _safe_archive_name(info.filename.rstrip("/"))
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                raise ConfigError("Evidence bundle contains a symbolic-link entry")
        names = [_safe_archive_name(info.filename) for info in infos if not info.is_dir()]
        if len(names) != len(set(names)):
            raise ConfigError("Evidence bundle contains duplicate paths")
        if _MANIFEST_NAME not in names:
            raise ConfigError(f"Evidence bundle is missing {_MANIFEST_NAME}")
        if any(info.flag_bits & 0x1 for info in infos):
            raise ConfigError("Encrypted evidence bundles are not supported")
        if any(info.file_size > _MAX_ENTRY_BYTES for info in infos):
            raise ConfigError("Evidence bundle contains an oversized entry")
        if sum(info.file_size for info in infos) > _MAX_TOTAL_BYTES:
            raise ConfigError("Evidence bundle exceeds the uncompressed size limit")
        try:
            manifest = strict_json_loads(archive.read(_MANIFEST_NAME))
        except (KeyError, ValueError, UnicodeDecodeError) as exc:
            raise ConfigError("Evidence bundle manifest is not valid UTF-8 JSON") from exc
        if not isinstance(manifest, dict):
            raise ConfigError("Evidence bundle manifest must be a JSON object")
        if manifest.get("schema_version") != 1 or manifest.get("kind") != (
            "scaffoldscope-evidence-bundle"
        ):
            raise ConfigError("Unsupported evidence bundle manifest")
        declared = manifest.get("files")
        if not isinstance(declared, dict):
            raise ConfigError("Evidence bundle manifest has no file map")
        if set(names) != set(declared) | {_MANIFEST_NAME}:
            raise ConfigError("Evidence bundle files do not match its manifest")
        missing_root = sorted(_ROOT_FILES - set(declared))
        if missing_root:
            raise ConfigError(f"Evidence bundle is missing required files: {missing_root}")
        unsupported = sorted(name for name in declared if not _allowed_evidence_name(str(name)))
        if unsupported:
            raise ConfigError(f"Evidence bundle contains unsupported evidence paths: {unsupported}")
        for name, metadata in declared.items():
            _safe_archive_name(str(name))
            if not isinstance(metadata, dict):
                raise ConfigError(f"Invalid evidence metadata for {name}")
            data = archive.read(str(name))
            if metadata.get("bytes") != len(data) or metadata.get("sha256") != _sha256(data):
                raise ConfigError(f"Evidence hash or size mismatch: {name}")
        identity = dict(manifest)
        declared_hash = identity.pop("bundle_hash", None)
        if not isinstance(declared_hash, str) or content_hash(identity) != declared_hash:
            raise ConfigError("Evidence bundle identity hash mismatch")
        # Hashes prove that the archive matches its own manifest, not that its
        # plan, aggregate results, and per-trial artifacts agree. Materialize
        # only already-validated regular files in an isolated temporary tree
        # and apply the same cross-file checker used before publication.
        with tempfile.TemporaryDirectory(prefix="scaffoldscope-bundle-verify-") as temporary:
            evidence_root = Path(temporary)
            try:
                for name in declared:
                    target = evidence_root / _safe_archive_name(str(name))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(str(name)))
            except OSError as exc:
                raise ConfigError(
                    "Could not materialize validated evidence for semantic verification"
                ) from exc
            valid, issues = check_experiment(evidence_root)
            if not valid:
                raise ConfigError(
                    "Evidence bundle semantic integrity check failed: " + "; ".join(issues)
                )
            experiment_manifest = load_json(evidence_root / "manifest.json")
            if not isinstance(experiment_manifest, dict) or (
                manifest.get("experiment") != experiment_manifest.get("experiment")
                or manifest.get("config_hash") != experiment_manifest.get("config_hash")
            ):
                raise ConfigError("Evidence bundle identity does not match its experiment manifest")
            if experiment_manifest.get("integrity_version") == 1:
                published_reports = {
                    name: (evidence_root / name).read_bytes() for name in _REPORT_FILES
                }
                summary = load_json(evidence_root / "summary.json")
                if not isinstance(summary, dict) or (
                    summary.get("schema_version") != 2
                    or summary.get("resampling_algorithm") != "sha256-counter-v1"
                ):
                    raise ConfigError(
                        "Evidence bundle uses an unsupported canonical report profile"
                    )
                write_report(evidence_root)
                changed_reports = sorted(
                    name
                    for name, published in published_reports.items()
                    if (evidence_root / name).read_bytes() != published
                )
                if changed_reports:
                    raise ConfigError(
                        "Evidence bundle derived reports do not match canonical regeneration: "
                        f"{changed_reports}"
                    )
        return manifest
