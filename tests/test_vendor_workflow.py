import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "skill-librarian" / "scripts" / "skill_librarian.py"
VENDOR_PATH = ROOT / "skill-librarian" / "scripts" / "skill_vendor.py"

control_spec = importlib.util.spec_from_file_location("skill_vendor_test_control", CONTROL_PATH)
control = importlib.util.module_from_spec(control_spec)
control_spec.loader.exec_module(control)

vendor_spec = importlib.util.spec_from_file_location("skill_vendor_test_module", VENDOR_PATH)
vendor = importlib.util.module_from_spec(vendor_spec)
vendor_spec.loader.exec_module(vendor)


class VendorWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.framework = self.root / "skill_manager"
        self.framework.mkdir()
        self.skill_dir = self.framework / "skill-librarian"
        self.skill_dir.mkdir()
        (self.skill_dir / "SKILL.md").write_text(
            "---\nname: skill-librarian\ndescription: test\n---\n",
            encoding="utf-8",
        )

        self.library = self.root / "personal-agent-skills"
        self.source = self.library / "coding_skills" / "demo-skill"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_text(
            "---\nname: demo-skill\ndescription: demo\n---\n",
            encoding="utf-8",
        )
        (self.source / "payload.txt").write_text("v1\n", encoding="utf-8")

        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", str(self.project)], check=True, capture_output=True)

        (self.framework / "deploy.json").write_text(
            json.dumps(
                {
                    "libraries": [str(self.library)],
                    "runtimes": {"codex": {"enabled": True}},
                }
            ),
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

    @property
    def target(self):
        return self.project / ".agents" / "skills" / "demo-skill"

    def create_vendor(self):
        registrar = mock.Mock(return_value=0)
        rc = vendor.vendor_create(
            "demo-skill",
            str(self.project),
            agents=["codex"],
            control=control,
            register_project=registrar,
        )
        self.assertEqual(rc, 0)
        registrar.assert_called_once_with(str(self.project.resolve()))

    def test_create_writes_real_snapshot_metadata_and_global_annotation(self):
        self.create_vendor()
        self.assertTrue(self.target.is_dir())
        self.assertFalse(self.target.is_symlink())
        metadata = json.loads((self.target / vendor.VENDOR_METADATA_FILENAME).read_text())
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["skill"], "demo-skill")
        self.assertEqual(metadata["runtime"], "codex")
        self.assertEqual(metadata["source_path"], "coding_skills/demo-skill")

        rows = control.list_target(
            "codex",
            "project",
            self.project / ".agents" / "skills",
            {"demo-skill": self.source.resolve()},
        )
        self.assertEqual(rows[0]["status"], control.STATUS_UNMANAGED)
        annotated = vendor.annotate_runtime_rows(rows)
        self.assertEqual(annotated[0]["status"], vendor.STATUS_VENDORED)

        result = vendor._inspect(control, "demo-skill", str(self.project), ["codex"])
        self.assertEqual(result["state"], vendor.STATE_SAME)
        self.assertFalse(result["local_modified"])

    def test_status_detects_source_local_and_diverged_changes(self):
        self.create_vendor()

        (self.source / "payload.txt").write_text("v2\n", encoding="utf-8")
        result = vendor._inspect(control, "demo-skill", str(self.project), ["codex"])
        self.assertEqual(result["state"], vendor.STATE_SOURCE_CHANGED)

        (self.source / "payload.txt").write_text("v1\n", encoding="utf-8")
        (self.target / "payload.txt").write_text("local\n", encoding="utf-8")
        result = vendor._inspect(control, "demo-skill", str(self.project), ["codex"])
        self.assertEqual(result["state"], vendor.STATE_LOCAL_MODIFIED)

        (self.source / "payload.txt").write_text("v3\n", encoding="utf-8")
        result = vendor._inspect(control, "demo-skill", str(self.project), ["codex"])
        self.assertEqual(result["state"], vendor.STATE_DIVERGED)

    def test_update_refreshes_clean_snapshot_and_refuses_local_changes_without_force(self):
        self.create_vendor()
        (self.source / "payload.txt").write_text("v2\n", encoding="utf-8")
        self.assertEqual(
            vendor.vendor_update(
                "demo-skill", str(self.project), agents=["codex"], control=control
            ),
            0,
        )
        self.assertEqual((self.target / "payload.txt").read_text(), "v2\n")
        result = vendor._inspect(control, "demo-skill", str(self.project), ["codex"])
        self.assertEqual(result["state"], vendor.STATE_SAME)

        (self.target / "payload.txt").write_text("local\n", encoding="utf-8")
        with self.assertRaises(vendor.VendorError):
            vendor.vendor_update(
                "demo-skill", str(self.project), agents=["codex"], control=control
            )

        self.assertEqual(
            vendor.vendor_update(
                "demo-skill",
                str(self.project),
                agents=["codex"],
                force=True,
                control=control,
            ),
            0,
        )
        self.assertEqual((self.target / "payload.txt").read_text(), "v2\n")

    def test_remove_refuses_local_changes_without_force(self):
        self.create_vendor()
        (self.target / "payload.txt").write_text("local\n", encoding="utf-8")
        with self.assertRaises(vendor.VendorError):
            vendor.vendor_remove(
                "demo-skill", str(self.project), agents=["codex"], control=control
            )
        self.assertTrue(self.target.exists())

        self.assertEqual(
            vendor.vendor_remove(
                "demo-skill",
                str(self.project),
                agents=["codex"],
                force=True,
                control=control,
            ),
            0,
        )
        self.assertFalse(self.target.exists())


if __name__ == "__main__":
    unittest.main()
