import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "skill-librarian" / "scripts" / "skill_librarian.py"
spec = importlib.util.spec_from_file_location("skill_librarian_cli", MODULE_PATH)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class SkillLibrarianTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.framework = self.root / "framework"
        self.framework.mkdir()
        (self.framework / "skill-librarian").mkdir()
        (self.framework / "skill-librarian" / "SKILL.md").write_text(
            "---\nname: skill-librarian\ndescription: test\n---\n"
        )

        self.library = self.root / "personal-agent-skills"
        self.library.mkdir()
        self.group = self.library / "3d_reconstruction_skills"
        self.group.mkdir()
        self.geometry = self.group / "reconstruction-geometry"
        self.geometry.mkdir()
        (self.geometry / "SKILL.md").write_text(
            "---\nname: reconstruction-geometry\ndescription: test\n---\n"
        )

        self.retired_group = self.library / "retire_skills"
        self.retired_group.mkdir()
        self.retired_agent = self.retired_group / "agent-state"
        self.retired_agent.mkdir()
        (self.retired_agent / "SKILL.md").write_text(
            "---\nname: agent-state\ndescription: retired\n---\n"
        )

        self.repo = self.root / "project"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)
        (self.framework / "deploy.json").write_text(
            json.dumps(
                {
                    "libraries": [str(self.library)],
                    "user_target": str(self.root / "user-skills"),
                }
            )
        )
        self.framework_patch = mock.patch.object(cli, "FRAMEWORK_ROOT", self.framework)
        self.skill_dir_patch = mock.patch.object(cli, "SKILL_DIR", self.framework / "skill-librarian")
        self.framework_patch.start()
        self.skill_dir_patch.start()

    def tearDown(self):
        self.skill_dir_patch.stop()
        self.framework_patch.stop()
        self.tmp.cleanup()

    def test_available_discovers_nested_skill_and_ignores_retired(self):
        skills, clashes = cli.discover_skills()
        self.assertEqual(set(skills), {"reconstruction-geometry", "skill-librarian"})
        self.assertEqual(skills["reconstruction-geometry"], self.geometry.resolve())
        self.assertNotIn("agent-state", skills)
        self.assertEqual(clashes, [])

    def test_flat_layout_remains_supported(self):
        flat = self.library / "flat-skill"
        flat.mkdir()
        (flat / "SKILL.md").write_text("---\nname: flat-skill\ndescription: test\n---\n")
        skills, _ = cli.discover_skills()
        self.assertEqual(skills["flat-skill"], flat.resolve())

    def test_configured_ignored_directory_is_pruned(self):
        archive = self.library / "archive"
        archive.mkdir()
        old = archive / "old-skill"
        old.mkdir()
        (old / "SKILL.md").write_text("---\nname: old-skill\ndescription: test\n---\n")
        (self.framework / "deploy.json").write_text(
            json.dumps(
                {
                    "libraries": [str(self.library)],
                    "user_target": str(self.root / "user-skills"),
                    "ignored_directories": ["retire_skills", "archive"],
                }
            )
        )
        skills, _ = cli.discover_skills()
        self.assertNotIn("old-skill", skills)
        self.assertNotIn("agent-state", skills)

    def test_project_mount_and_unmount_are_link_only(self):
        self.assertEqual(cli.mount("reconstruction-geometry", project=str(self.repo)), 0)
        link = self.repo / ".agents" / "skills" / "reconstruction-geometry"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.geometry.resolve())
        self.assertEqual(cli.unmount("reconstruction-geometry", project=str(self.repo)), 0)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(self.geometry.exists())

    def test_user_mount(self):
        self.assertEqual(cli.mount("reconstruction-geometry", user=True), 0)
        link = self.root / "user-skills" / "reconstruction-geometry"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.geometry.resolve())

    def test_mount_refuses_real_directory(self):
        target = self.repo / ".agents" / "skills" / "reconstruction-geometry"
        target.mkdir(parents=True)
        with self.assertRaises(cli.LibrarianError):
            cli.mount("reconstruction-geometry", project=str(self.repo))
        self.assertTrue(target.is_dir())
        self.assertFalse(target.is_symlink())

    def test_unmount_refuses_real_directory(self):
        target = self.repo / ".agents" / "skills" / "reconstruction-geometry"
        target.mkdir(parents=True)
        with self.assertRaises(cli.LibrarianError):
            cli.unmount("reconstruction-geometry", project=str(self.repo))
        self.assertTrue(target.exists())

    def test_doctor_detects_duplicate_user_and_project_mount(self):
        cli.mount("reconstruction-geometry", user=True)
        cli.mount("reconstruction-geometry", project=str(self.repo))
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            rc = cli.doctor()
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 1)

    def test_doctor_detects_tracked_project_mount(self):
        cli.mount("reconstruction-geometry", project=str(self.repo))
        subprocess.run(
            ["git", "-C", str(self.repo), "add", ".agents/skills/reconstruction-geometry"],
            check=True,
            capture_output=True,
        )
        self.assertEqual(cli.doctor(project=str(self.repo)), 1)

    def test_doctor_flags_mount_into_retired_subtree(self):
        target_dir = self.repo / ".agents" / "skills"
        target_dir.mkdir(parents=True)
        os.symlink(self.retired_agent.resolve(), target_dir / "agent-state")
        self.assertEqual(cli.doctor(project=str(self.repo)), 1)

    def test_doctor_healthy_for_nested_project_mount(self):
        cli.mount("reconstruction-geometry", project=str(self.repo))
        self.assertEqual(cli.doctor(project=str(self.repo)), 0)


if __name__ == "__main__":
    unittest.main()
