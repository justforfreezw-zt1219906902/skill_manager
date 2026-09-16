import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "skill-librarian" / "scripts" / "skill_librarian.py"
IMPORT_PATH = ROOT / "skill-librarian" / "scripts" / "skill_import.py"

control_spec = importlib.util.spec_from_file_location("skill_librarian_import_control", CONTROL_PATH)
control = importlib.util.module_from_spec(control_spec)
sys.modules[control_spec.name] = control
control_spec.loader.exec_module(control)

import_spec = importlib.util.spec_from_file_location("skill_import_workflow", IMPORT_PATH)
import_mod = importlib.util.module_from_spec(import_spec)
sys.modules[import_spec.name] = import_mod
import_spec.loader.exec_module(import_mod)


class ImportWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.framework = self.root / "framework"
        self.framework.mkdir()
        self.skill_dir = self.framework / "skill-librarian"
        self.skill_dir.mkdir()
        (self.skill_dir / "SKILL.md").write_text(
            "---\nname: skill-librarian\ndescription: test\n---\n",
            encoding="utf-8",
        )

        self.library = self.root / "personal-agent-skills"
        self.library.mkdir()
        (self.framework / "deploy.json").write_text(
            json.dumps({"libraries": [str(self.library)], "ignored_directories": ["retire_skills"]}),
            encoding="utf-8",
        )

        self.framework_patch = mock.patch.object(control, "FRAMEWORK_ROOT", self.framework)
        self.skill_dir_patch = mock.patch.object(control, "SKILL_DIR", self.skill_dir)
        self.framework_patch.start()
        self.skill_dir_patch.start()

    def tearDown(self):
        self.skill_dir_patch.stop()
        self.framework_patch.stop()
        self.tmp.cleanup()

    def make_source_skill(self, name="foo", folder="skills/foo"):
        source_root = self.root / "external"
        skill = source_root / folder
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: external\n---\n",
            encoding="utf-8",
        )
        (skill / "payload.txt").write_text("payload", encoding="utf-8")
        return source_root, skill

    def test_import_local_skill_writes_canonical_copy_and_provenance(self):
        source_root, _source_skill = self.make_source_skill()

        rc = import_mod.import_skill(str(source_root), "foo", control=control)
        self.assertEqual(rc, 0)

        destination = self.library / "imported" / "foo"
        self.assertTrue(destination.is_dir())
        self.assertEqual((destination / "payload.txt").read_text(), "payload")
        metadata = json.loads((destination / ".skill-source.json").read_text())
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["type"], "local")
        self.assertIsNone(metadata["source"])
        self.assertEqual(metadata["source_path"], "skills/foo")
        self.assertEqual(metadata["skill"], "foo")
        self.assertEqual(metadata["acquired_via"], "local")
        self.assertEqual(control.discover_skills()[0]["foo"], destination.resolve())

    def test_unified_cli_dispatches_import_without_runtime_mount(self):
        source_root, _ = self.make_source_skill()

        rc = control.main(["import", str(source_root), "--skill", "foo"])
        self.assertEqual(rc, 0)

        destination = self.library / "imported" / "foo"
        self.assertTrue(destination.is_dir())
        self.assertTrue((destination / ".skill-source.json").is_file())
        self.assertFalse((self.root / "codex-user-skills" / "foo").exists())

    def test_import_dry_run_does_not_write(self):
        source_root, _ = self.make_source_skill()
        rc = import_mod.import_skill(str(source_root), "foo", control=control, dry_run=True)
        self.assertEqual(rc, 0)
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_import_rejects_existing_canonical_name(self):
        existing = self.library / "existing" / "foo"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text(
            "---\nname: foo\ndescription: existing\n---\n",
            encoding="utf-8",
        )
        source_root, _ = self.make_source_skill()

        with self.assertRaises(import_mod.ImportWorkflowError):
            import_mod.import_skill(str(source_root), "foo", control=control)
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_import_rejects_env_secret_file(self):
        source_root, skill = self.make_source_skill()
        (skill / ".env").write_text("TOKEN=secret", encoding="utf-8")

        with self.assertRaises(import_mod.ImportWorkflowError):
            import_mod.import_skill(str(source_root), "foo", control=control)
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_import_allows_env_example(self):
        source_root, skill = self.make_source_skill()
        (skill / ".env.example").write_text("TOKEN=", encoding="utf-8")

        self.assertEqual(import_mod.import_skill(str(source_root), "foo", control=control), 0)
        self.assertTrue((self.library / "imported" / "foo" / ".env.example").is_file())

    def test_import_rejects_transient_dependency_directory(self):
        source_root, skill = self.make_source_skill()
        (skill / "node_modules").mkdir()
        (skill / "node_modules" / "package.js").write_text("x", encoding="utf-8")

        with self.assertRaises(import_mod.ImportWorkflowError):
            import_mod.import_skill(str(source_root), "foo", control=control)
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_import_strips_nested_vcs_metadata(self):
        source_root, skill = self.make_source_skill()
        (skill / ".git").mkdir()
        (skill / ".git" / "config").write_text("fake", encoding="utf-8")

        self.assertEqual(import_mod.import_skill(str(source_root), "foo", control=control), 0)
        destination = self.library / "imported" / "foo"
        self.assertFalse((destination / ".git").exists())
        self.assertTrue((destination / "payload.txt").is_file())

    def test_import_overwrites_untrusted_incoming_provenance_with_own_metadata(self):
        source_root, skill = self.make_source_skill()
        (skill / ".skill-source.json").write_text(
            json.dumps({"source": "https://evil.invalid/fake.git", "revision": "fake"}),
            encoding="utf-8",
        )

        self.assertEqual(import_mod.import_skill(str(source_root), "foo", control=control), 0)
        metadata = json.loads((self.library / "imported" / "foo" / ".skill-source.json").read_text())
        self.assertNotEqual(metadata.get("source"), "https://evil.invalid/fake.git")
        self.assertEqual(metadata["skill"], "foo")
        self.assertEqual(metadata["schema_version"], 1)

    def test_import_rolls_back_destination_when_discovery_verification_fails(self):
        source_root, _ = self.make_source_skill()
        original_discover = control.discover_skills
        calls = {"count": 0}

        def fake_discover():
            calls["count"] += 1
            if calls["count"] == 1:
                return original_discover()
            return {}, []

        with mock.patch.object(control, "discover_skills", side_effect=fake_discover):
            with self.assertRaises(import_mod.ImportWorkflowError):
                import_mod.import_skill(str(source_root), "foo", control=control)

        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_import_remote_git_records_exact_revision(self):
        repo = self.root / "git-source"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test User"], check=True)
        skill = repo / "skills" / "foo"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: foo\ndescription: git\n---\n", encoding="utf-8")
        (skill / "payload.txt").write_text("git payload", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
        revision = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        self.assertEqual(
            import_mod.import_skill(repo.resolve().as_uri(), "foo", control=control),
            0,
        )
        metadata = json.loads((self.library / "imported" / "foo" / ".skill-source.json").read_text())
        self.assertEqual(metadata["type"], "git")
        self.assertEqual(metadata["revision"], revision)
        self.assertEqual(metadata["source_path"], "skills/foo")
        self.assertFalse(metadata["dirty"])
        self.assertEqual(metadata["acquired_via"], "git")
        self.assertIsNone(metadata["source"])


if __name__ == "__main__":
    unittest.main()
