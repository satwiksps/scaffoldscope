from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from scaffoldscope.errors import SandboxError
from scaffoldscope.sandbox import EvaluationResult, LocalSandbox, RestrictedSandbox
from scaffoldscope.schema import ConstraintSpec, SandboxConfig, TaskSpec


class SandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "module.py").write_text("def value():\n    return 1\n", encoding="utf-8")
        (self.root / "canary.txt").write_text("keep\n", encoding="utf-8")
        self.task = TaskSpec(
            id="sandbox-task",
            workspace=self.root,
            problem="Fix value.",
            constraints=(
                ConstraintSpec(
                    "canary",
                    "Do not edit canary.",
                    {"type": "file_unchanged", "path": "canary.txt"},
                ),
            ),
            test_command=("{python}", "-c", "import module; assert module.value() == 2"),
        )
        self.sandbox = LocalSandbox(self.root, self.task, SandboxConfig())

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_path_traversal_is_rejected(self) -> None:
        result = self.sandbox.invoke("read_file", {"path": "../secret.txt"})
        self.assertFalse(result.ok)
        self.assertIn("escapes", result.content)

    def test_replace_then_evaluate_and_diff(self) -> None:
        result = self.sandbox.invoke(
            "replace",
            {
                "path": "module.py",
                "old_text": "    return 1\n",
                "new_text": "    return 2\n",
            },
        )
        self.assertTrue(result.ok)
        evaluation = self.sandbox.evaluate()
        self.assertTrue(evaluation.passed)
        self.assertEqual(evaluation.behavioral_adherence, 1.0)
        patch = self.sandbox.patch()
        self.assertIn("+    return 2", patch)

    def test_model_observation_excludes_nondeterministic_tool_timing(self) -> None:
        result = self.sandbox.invoke("run_tests", {})

        self.assertTrue(result.ok)
        self.assertGreaterEqual(result.duration_seconds, 0.0)
        self.assertNotIn("duration_seconds", result.model_content())
        self.assertIn("duration_seconds", result.to_dict())

    def test_run_tests_normalizes_model_facing_newlines(self) -> None:
        evaluation = EvaluationResult(True, 0, "one\r\ntwo\rthree\n", 0.25, {}, {})
        with patch.object(self.sandbox, "_run_test_command", return_value=evaluation):
            result = self.sandbox.invoke("run_tests", {})
        self.assertEqual(result.content, "one\ntwo\nthree\n")

    def test_process_output_is_bounded_while_child_runs(self) -> None:
        task = TaskSpec(
            id="bounded-output",
            workspace=self.root,
            problem="Exercise bounded evaluator output.",
            constraints=(),
            test_command=("{python}", "-c", "print('x' * 100000)"),
        )
        sandbox = LocalSandbox(
            self.root,
            task,
            SandboxConfig(max_process_output_chars=128),
        )

        evaluation = sandbox.evaluate()

        self.assertTrue(evaluation.passed)
        self.assertLessEqual(len(evaluation.output), 128)
        self.assertIn("truncated", evaluation.output)

    def test_timeout_terminates_descendant_processes(self) -> None:
        script = self.root / "spawn_tree.py"
        script.write_text(
            """\
import subprocess
import sys
import time

child = '''\
from pathlib import Path
import time
Path("descendant-ready").write_text("ready", encoding="utf-8")
time.sleep(2)
Path("descendant-survived").write_text("bad", encoding="utf-8")
'''
subprocess.Popen([sys.executable, "-c", child])
time.sleep(30)
""",
            encoding="utf-8",
        )
        task = TaskSpec(
            id="process-tree-timeout",
            workspace=self.root,
            problem="Exercise evaluator timeout cleanup.",
            constraints=(),
            test_command=("{python}", script.name),
        )
        sandbox = LocalSandbox(
            self.root,
            task,
            SandboxConfig(test_timeout_seconds=1.0),
        )

        evaluation = sandbox.evaluate()

        self.assertFalse(evaluation.passed)
        self.assertIsNone(evaluation.returncode)
        self.assertIn("timed out", evaluation.output)
        self.assertTrue((self.root / "descendant-ready").is_file())
        time.sleep(1.25)
        self.assertFalse((self.root / "descendant-survived").exists())

    def test_protected_directory_is_immutable_and_audited(self) -> None:
        tests_dir = self.root / "tests"
        tests_dir.mkdir()
        evaluator = tests_dir / "test_hidden.py"
        evaluator.write_text("SENTINEL = True\n", encoding="utf-8")
        task = TaskSpec(
            id="protected-directory",
            workspace=self.root,
            problem="Exercise evaluator protection.",
            constraints=(),
            test_command=("{python}", "-c", "import module; assert module.value() == 1"),
            protected_paths=("tests",),
        )
        sandbox = LocalSandbox(self.root, task, SandboxConfig())

        rejected = sandbox.invoke("write_file", {"path": "tests/new_test.py", "content": "pass\n"})
        self.assertFalse(rejected.ok)
        self.assertTrue(sandbox.evaluate().evaluator_integrity)

        evaluator.write_text("SENTINEL = False\n", encoding="utf-8")
        evaluation = sandbox.evaluate()
        self.assertFalse(evaluation.passed)
        self.assertFalse(evaluation.evaluator_integrity)

    def test_tool_booleans_are_strict(self) -> None:
        result = self.sandbox.invoke(
            "search", {"path": ".", "query": "value", "case_sensitive": "false"}
        )
        self.assertFalse(result.ok)
        self.assertIn("boolean", result.content)

    def test_symbol_search_and_treatment_tool_gate(self) -> None:
        symbols = self.sandbox.invoke("search_symbols", {"symbol": "value"})
        self.assertTrue(symbols.ok)
        self.assertIn("module.py:1:function:value", symbols.content)

        restricted = RestrictedSandbox(
            self.sandbox,
            ("list_files", "read_file", "search_symbols", "run_tests"),
        )
        denied = restricted.invoke(
            "replace",
            {"path": "module.py", "old_text": "return 1", "new_text": "return 2"},
        )
        self.assertFalse(denied.ok)
        self.assertIn("not available in this treatment", denied.content)
        self.assertEqual(
            restricted.available_tools,
            ("list_files", "read_file", "search_symbols", "run_tests"),
        )

    def test_non_git_patch_never_silently_drops_unsupported_changes(self) -> None:
        binary = self.root / "asset.bin"
        binary.write_bytes(b"\x00original")
        sandbox = LocalSandbox(self.root, self.task, SandboxConfig())
        binary.write_bytes(b"\x00changed")
        with self.assertRaises(SandboxError):
            sandbox.patch()

        binary.write_bytes(b"\x00original")
        sandbox = LocalSandbox(self.root, self.task, SandboxConfig())
        written = sandbox.invoke("write_file", {"path": "empty.txt", "content": ""})
        self.assertTrue(written.ok)
        patch_text = sandbox.patch()
        self.assertIn("new file mode 100644", patch_text)

        git_executable = shutil.which("git")
        if git_executable is None:
            self.skipTest("git is required to apply the exported patch")
        apply_root = self.root / "apply-empty"
        apply_root.mkdir()
        checked = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [git_executable, "apply", "--check", "-"],
            cwd=apply_root,
            input=patch_text,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(checked.returncode, 0, checked.stderr)
        applied = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [git_executable, "apply", "-"],
            cwd=apply_root,
            input=patch_text.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr.decode(errors="replace"))
        self.assertEqual((apply_root / "empty.txt").read_bytes(), b"")

        obsolete = self.root / "obsolete.txt"
        obsolete.write_bytes(b"")
        deleting = LocalSandbox(self.root, self.task, SandboxConfig())
        obsolete.unlink()
        delete_patch = deleting.patch()
        self.assertIn("deleted file mode 100644", delete_patch)
        (apply_root / "obsolete.txt").write_bytes(b"")
        deleted = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [git_executable, "apply", "-"],
            cwd=apply_root,
            input=delete_patch,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(deleted.returncode, 0, deleted.stderr)
        self.assertFalse((apply_root / "obsolete.txt").exists())

    def test_non_git_patch_preserves_missing_final_newline(self) -> None:
        source = self.root / "without-newline.txt"
        source.write_bytes(b"old")
        sandbox = LocalSandbox(self.root, self.task, SandboxConfig())
        replaced = sandbox.invoke(
            "replace",
            {
                "path": "without-newline.txt",
                "old_text": "old",
                "new_text": "new",
            },
        )
        self.assertTrue(replaced.ok)
        patch_text = sandbox.patch()
        self.assertEqual(patch_text.count("\\ No newline at end of file"), 2)

        git_executable = shutil.which("git")
        if git_executable is None:
            self.skipTest("git is required to apply the exported patch")
        apply_root = self.root / "apply-no-newline"
        apply_root.mkdir()
        (apply_root / "without-newline.txt").write_bytes(b"old")
        applied = subprocess.run(  # noqa: S603 - executable resolved by shutil.which
            [git_executable, "apply", "-"],
            cwd=apply_root,
            input=patch_text.encode("utf-8"),
            capture_output=True,
            check=False,
        )
        self.assertEqual(applied.returncode, 0, applied.stderr.decode(errors="replace"))
        self.assertEqual((apply_root / "without-newline.txt").read_bytes(), b"new")

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on Windows")
    def test_atomic_write_preserves_existing_executable_mode(self) -> None:
        executable = self.root / "script.sh"
        executable.write_text("#!/bin/sh\necho old\n", encoding="utf-8")
        executable.chmod(0o755)
        sandbox = LocalSandbox(self.root, self.task, SandboxConfig())

        result = sandbox.invoke(
            "replace",
            {
                "path": "script.sh",
                "old_text": "echo old",
                "new_text": "echo new",
            },
        )

        self.assertTrue(result.ok)
        self.assertEqual(stat.S_IMODE(executable.stat().st_mode), 0o755)

    @unittest.skipIf(os.name == "nt", "POSIX mode bits are not meaningful on Windows")
    def test_atomic_write_uses_exported_mode_for_new_file(self) -> None:
        created = self.sandbox.invoke(
            "write_file",
            {"path": "created.py", "content": "VALUE = 1\n"},
        )

        self.assertTrue(created.ok)
        self.assertEqual(stat.S_IMODE((self.root / "created.py").stat().st_mode), 0o644)
        self.assertIn("new file mode 100644", self.sandbox.patch())


if __name__ == "__main__":
    unittest.main()
