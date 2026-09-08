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
        self.library = self.root / "library"
        self.library.mkdir()
        self.agent = self.library / "agent-state"
        self.agent.mkdir()
        (self.agent / "SKILL.md").write_text(
            "---\nname: agent-state\ndescription: test\n---\n"
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

    def test_available_discovers_personal_library(self):
        skills, clashes = cli.discover_skills()
        self.assertEqual(set(skills), {"agent-state", "skill-librarian"})
        self.assertEqual(clashes, [])

    def test_project_mount_and_unmount_are_link_only(self):
        self.assertEqual(cli.mount("agent-state", project=str(self.repo)), 0)
        link = self.repo / ".agents" / "skills" / "agent-state"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.agent.resolve())
        self.assertEqual(cli.unmount("agent-state", project=str(self.repo)), 0)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(self.agent.exists())

    def test_user_mount(self):
        self.assertEqual(cli.mount("agent-state", user=True), 0)
        link = self.root / "user-skills" / "agent-state"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), self.agent.resolve())

    def test_mount_refuses_real_directory(self):
        target = self.repo / ".agents" / "skills" / "agent-state"
        target.mkdir(parents=True)
        with self.assertRaises(cli.LibrarianError):
            cli.mount("agent-state", project=str(self.repo))
        self.assertTrue(target.is_dir())
        self.assertFalse(target.is_symlink())

    def test_unmount_refuses_real_directory(self):
        target = self.repo / ".agents" / "skills" / "agent-state"
        target.mkdir(parents=True)
        with self.assertRaises(cli.LibrarianError):
            cli.unmount("agent-state", project=str(self.repo))
        self.assertTrue(target.exists())

    def test_doctor_detects_duplicate_user_and_project_mount(self):
        cli.mount("agent-state", user=True)
        cli.mount("agent-state", project=str(self.repo))
        cwd = os.getcwd()
        try:
            os.chdir(self.repo)
            rc = cli.doctor()
        finally:
            os.chdir(cwd)
        self.assertEqual(rc, 1)

    def test_doctor_detects_tracked_project_mount(self):
        cli.mount("agent-state", project=str(self.repo))
        subprocess.run(
            ["git", "-C", str(self.repo), "add", ".agents/skills/agent-state"],
            check=True,
            capture_output=True,
        )
        self.assertEqual(cli.doctor(project=str(self.repo)), 1)

    def test_doctor_healthy_for_project_only_mount(self):
        cli.mount("agent-state", project=str(self.repo))
        self.assertEqual(cli.doctor(project=str(self.repo)), 0)


if __name__ == "__main__":
    unittest.main()
