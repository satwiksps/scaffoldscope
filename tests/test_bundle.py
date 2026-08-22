from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from scaffoldscope.bundle import create_evidence_bundle, verify_evidence_bundle
from scaffoldscope.errors import ConfigError
from scaffoldscope.jsonutil import content_hash
from scaffoldscope.report import write_report
from scaffoldscope.runner import run_experiment
from scaffoldscope.schema import RunConfig

_REPORT_FILES = (
    "paired-comparisons.csv",
    "report.html",
    "report.md",
    "summary.csv",
    "summary.json",
)
DEMO = Path(__file__).resolve().parents[1] / "src" / "scaffoldscope" / "demo"


class BundleTests(unittest.TestCase):
    def _experiment(self, root: Path) -> Path:
        experiment = root / "run"
        trial = experiment / "trials" / "trial-a"
        workspace = trial / "workspace"
        workspace.mkdir(parents=True)
        (workspace / "secret.py").write_text("not bundled\n", encoding="utf-8")
        plan = {
            "trial_id": "trial-a",
            "trial_hash": content_hash(
                {
                    "config_hash": "config-hash",
                    "task_id": "task-a",
                    "variant_id": "none",
                    "replicate": 1,
                }
            ),
            "task_id": "task-a",
            "variant_id": "none",
            "replicate": 1,
        }
        manifest = {
            "schema_version": 1,
            "scaffoldscope_version": "test",
            "experiment": "bundle-test",
            "config_hash": "config-hash",
            "implementation_hash": "implementation-hash",
            "task_source_hashes": {"task-a": "source-hash"},
            "tasks": ["task-a"],
            "variants": ["none"],
            "replicates": [1],
            "trial_count": 1,
        }
        result = {
            **plan,
            "config_hash": "config-hash",
            "implementation_hash": "implementation-hash",
            "task_source_hash": "source-hash",
            "scaffoldscope_version": "test",
            "experiment": "bundle-test",
            "status": "resolved",
            "infrastructure_valid": True,
            "evaluation_valid": True,
            "solved": True,
            "governed_solved": True,
            "wall_seconds": 0.0,
            "artifacts": {
                "trace": "trials/trial-a/events.jsonl",
                "patch": "trials/trial-a/patch.diff",
                "result": "trials/trial-a/result.json",
                "workspace": "trials/trial-a/workspace",
            },
        }
        trace = (
            b'{"schema_version":1,"sequence":1,"timestamp":"2026-08-15T00:00:00+00:00",'
            b'"type":"trial_finished","payload":{"status":"resolved","solved":true,'
            b'"wall_seconds":0.0}}\n'
        )
        patch = b"diff --git a/a b/a\n"
        result["patch_sha256"] = hashlib.sha256(patch).hexdigest()
        result["artifact_hashes"] = {
            "trace_sha256": hashlib.sha256(trace).hexdigest(),
            "patch_sha256": hashlib.sha256(patch).hexdigest(),
        }
        (experiment / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (experiment / "config.resolved.json").write_text(
            json.dumps(
                {
                    "resolved": {
                        "config_hash": "config-hash",
                        "implementation_hash": "implementation-hash",
                        "task_source_hashes": {"task-a": "source-hash"},
                    }
                }
            ),
            encoding="utf-8",
        )
        (experiment / "plan.jsonl").write_text(json.dumps(plan) + "\n", encoding="utf-8")
        (experiment / "episodes.jsonl").write_text(json.dumps(result) + "\n", encoding="utf-8")
        pricing = {
            "model": "test-model",
            "input_price_per_million": 0.0,
            "output_price_per_million": 0.0,
            "cache_read_price_per_million": None,
            "cache_write_price_per_million": None,
            "currency": "USD",
            "source": "test fixture",
        }
        (experiment / "pricing.json").write_text(
            json.dumps({**pricing, "hash": content_hash(pricing)}), encoding="utf-8"
        )
        for name, content in {
            "paired-comparisons.csv": "strategy\n",
            "report.html": "<!doctype html><title>test</title>\n",
            "report.md": "# Test report\n",
            "summary.csv": "strategy\n",
            "summary.json": "{}\n",
        }.items():
            (experiment / name).write_text(content, encoding="utf-8")
        (trial / "events.jsonl").write_bytes(trace)
        (trial / "patch.diff").write_bytes(patch)
        (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")
        return experiment

    def test_bundle_excludes_workspaces_and_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            aborted = experiment / "aborted-attempts" / "trial-a--interrupted"
            (aborted / "workspace").mkdir(parents=True)
            (aborted / "events.jsonl").write_text('{"type":"model_request"}\n', encoding="utf-8")
            (aborted / "workspace" / "private.py").write_text("excluded\n", encoding="utf-8")
            output = root / "evidence.zip"

            created = create_evidence_bundle(experiment, output)
            verified = verify_evidence_bundle(output)

            self.assertEqual(created["bundle_hash"], verified["bundle_hash"])
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
            self.assertFalse(any("workspace" in name for name in names))
            self.assertIn("trials/trial-a/result.json", names)
            self.assertIn(
                "aborted-attempts/trial-a--interrupted/events.jsonl",
                names,
            )

    def test_bundle_repairs_tampered_reports_from_frozen_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            write_report(experiment)
            canonical = {name: (experiment / name).read_bytes() for name in _REPORT_FILES}
            for name in _REPORT_FILES:
                (experiment / name).write_text(f"tampered {name}\n", encoding="utf-8")
            output = root / "evidence.zip"

            create_evidence_bundle(experiment, output)

            with zipfile.ZipFile(output) as archive:
                for name, expected in canonical.items():
                    self.assertEqual((experiment / name).read_bytes(), expected)
                    self.assertEqual(archive.read(name), expected)

    def test_bundle_remains_deterministic_when_reports_are_regenerated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            first = root / "first.zip"
            second = root / "second.zip"

            create_evidence_bundle(experiment, first)
            create_evidence_bundle(experiment, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_bundle_refuses_overwrite_before_repairing_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            tampered = b"tampered report\n"
            (experiment / "report.md").write_bytes(tampered)
            output = root / "evidence.zip"
            output.write_bytes(b"existing archive")

            with self.assertRaisesRegex(ConfigError, "Refusing to overwrite"):
                create_evidence_bundle(experiment, output)

            self.assertEqual(output.read_bytes(), b"existing archive")
            self.assertEqual((experiment / "report.md").read_bytes(), tampered)

    def test_bundle_write_failure_leaves_no_partial_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            experiment = self._experiment(root)
            output = root / "evidence.zip"
            real_writestr = zipfile.ZipFile.writestr
            calls = 0

            def fail_second_write(
                archive: zipfile.ZipFile,
                member: object,
                data: object,
                *args: object,
                **kwargs: object,
            ) -> object:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("archive write fixture failed")
                return real_writestr(archive, member, data, *args, **kwargs)

            with (
                patch.object(zipfile.ZipFile, "writestr", new=fail_second_write),
                self.assertRaisesRegex(OSError, "archive write fixture failed"),
            ):
                create_evidence_bundle(experiment, output)

            self.assertFalse(output.exists())
            create_evidence_bundle(experiment, output)
            verify_evidence_bundle(output)

    def test_verify_rejects_an_identity_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.zip"
            identity = {
                "schema_version": 1,
                "kind": "scaffoldscope-evidence-bundle",
                "experiment": "x",
                "config_hash": "y",
                "files": {},
            }
            manifest = {**identity, "bundle_hash": content_hash(identity) + "tampered"}
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("BUNDLE-MANIFEST.json", json.dumps(manifest))
            with self.assertRaises(ConfigError):
                verify_evidence_bundle(path)

    def test_verify_rejects_self_consistent_hashes_with_invalid_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.zip"
            tampered = root / "tampered.zip"
            create_evidence_bundle(self._experiment(root), original)

            with zipfile.ZipFile(original) as archive:
                payloads = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if name != "BUNDLE-MANIFEST.json"
                }
                manifest = json.loads(archive.read("BUNDLE-MANIFEST.json"))
            rows = [json.loads(line) for line in payloads["episodes.jsonl"].splitlines()]
            rows[0]["variant_id"] = "undeclared-treatment"
            payloads["episodes.jsonl"] = (
                "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n"
            ).encode("utf-8")
            manifest["files"]["episodes.jsonl"] = {
                "sha256": hashlib.sha256(payloads["episodes.jsonl"]).hexdigest(),
                "bytes": len(payloads["episodes.jsonl"]),
            }
            manifest_identity = dict(manifest)
            manifest_identity.pop("bundle_hash")
            manifest["bundle_hash"] = content_hash(manifest_identity)
            with zipfile.ZipFile(tampered, "w") as archive:
                archive.writestr("BUNDLE-MANIFEST.json", json.dumps(manifest))
                for name, payload in payloads.items():
                    archive.writestr(name, payload)

            with self.assertRaisesRegex(ConfigError, "semantic integrity"):
                verify_evidence_bundle(tampered)

    def test_verify_rejects_self_hashed_fabricated_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.zip"
            tampered = root / "tampered.zip"
            project = root / "demo"
            shutil.copytree(DEMO, project)
            experiment = run_experiment(RunConfig.load(project / "experiment.json")).experiment_dir
            write_report(experiment)
            create_evidence_bundle(experiment, original)

            with zipfile.ZipFile(original) as archive:
                payloads = {
                    name: archive.read(name)
                    for name in archive.namelist()
                    if name != "BUNDLE-MANIFEST.json"
                }
                bundle_manifest = json.loads(archive.read("BUNDLE-MANIFEST.json"))
            payloads["report.md"] = b"# Fabricated conclusion\n\nTreatment wins.\n"
            bundle_manifest["files"]["report.md"] = {
                "sha256": hashlib.sha256(payloads["report.md"]).hexdigest(),
                "bytes": len(payloads["report.md"]),
            }
            identity = dict(bundle_manifest)
            identity.pop("bundle_hash")
            bundle_manifest["bundle_hash"] = content_hash(identity)
            with zipfile.ZipFile(tampered, "w") as archive:
                archive.writestr("BUNDLE-MANIFEST.json", json.dumps(bundle_manifest))
                for name, payload in payloads.items():
                    archive.writestr(name, payload)

            with self.assertRaisesRegex(ConfigError, "canonical regeneration"):
                verify_evidence_bundle(tampered)

    def test_verify_rejects_symbolic_link_entries_without_extracting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "link.zip"
            link = zipfile.ZipInfo("trace-link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(link, "outside")
            with self.assertRaisesRegex(ConfigError, "symbolic-link"):
                verify_evidence_bundle(path)

    def test_verify_rejects_noncanonical_archive_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "alias.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("./manifest.json", "{}")
            with self.assertRaisesRegex(ConfigError, "Unsafe evidence-bundle path"):
                verify_evidence_bundle(path)

    def test_verify_rejects_declared_non_evidence_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            original = root / "original.zip"
            tampered = root / "tampered.zip"
            create_evidence_bundle(self._experiment(root), original)
            with zipfile.ZipFile(original) as archive:
                payloads = {name: archive.read(name) for name in archive.namelist()}
            manifest = json.loads(payloads.pop("BUNDLE-MANIFEST.json"))
            payloads["private-notes.txt"] = b"not evidence\n"
            manifest["files"]["private-notes.txt"] = {
                "sha256": hashlib.sha256(payloads["private-notes.txt"]).hexdigest(),
                "bytes": len(payloads["private-notes.txt"]),
            }
            identity = dict(manifest)
            identity.pop("bundle_hash")
            manifest["bundle_hash"] = content_hash(identity)
            with zipfile.ZipFile(tampered, "w") as archive:
                archive.writestr("BUNDLE-MANIFEST.json", json.dumps(manifest))
                for name, payload in payloads.items():
                    archive.writestr(name, payload)

            with self.assertRaisesRegex(ConfigError, "unsupported evidence paths"):
                verify_evidence_bundle(tampered)


if __name__ == "__main__":
    unittest.main()
