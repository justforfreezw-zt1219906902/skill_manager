import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "skill-librarian" / "scripts" / "skill_librarian.py"
spec = importlib.util.spec_from_file_location("skill_librarian_adopt_cli", MODULE_PATH)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class AdoptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        self.framework = self.root / "framework"
        self.framework.mkdir()
        self.skill_dir = self.framework / "skill-librarian"
        self.skill_dir.mkdir()
        (self.skill_dir / "SKILL.md").write_text(
            "---\nname: skill-librarian\ndescription: test\n---\n"
        )

        self.library = self.root / "personal-agent-skills"
        self.library.mkdir()
        self.codex_user = self.root / "codex-user-skills"
        self.claude_user = self.root / "claude-user-skills"

        self.repo = self.root / "project"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)

        (self.framework / "deploy.json").write_text(
            json.dumps(
                {
                    "libraries": [str(self.library)],
                    "user_target": str(self.codex_user),
                    "runtimes": {
                        "codex": {"enabled": True},
                        "claude-code": {
                            "enabled": True,
                            "user_target": str(self.claude_user),
                        },
                    },
                }
            )
        )

        self.framework_patch = mock.patch.object(cli, "FRAMEWORK_ROOT", self.framework)
        self.skill_dir_patch = mock.patch.object(cli, "SKILL_DIR", self.skill_dir)
        self.framework_patch.start()
        self.skill_dir_patch.start()

    def tearDown(self):
        self.skill_dir_patch.stop()
        self.framework_patch.stop()
        self.tmp.cleanup()

    def read_cfg(self):
        return json.loads((self.framework / "deploy.json").read_text())

    def write_cfg(self, cfg):
        (self.framework / "deploy.json").write_text(json.dumps(cfg))

    def make_runtime_skill(self, name="foo", frontmatter_name=None, target=None):
        target = target or self.codex_user
        skill = target / name
        skill.mkdir(parents=True)
        fm_name = frontmatter_name or name
        (skill / "SKILL.md").write_text(
            f"---\nname: {fm_name}\ndescription: unmanaged test skill\n---\n"
        )
        (skill / "payload.txt").write_text("payload")
        return skill

    def test_adopt_unmanaged_real_directory(self):
        runtime_skill = self.make_runtime_skill("foo")

        self.assertEqual(cli.adopt("foo", user=True), 0)

        destination = self.library / "imported" / "foo"
        self.assertTrue(destination.is_dir())
        self.assertEqual((destination / "payload.txt").read_text(), "payload")
        self.assertTrue(runtime_skill.is_symlink())
        self.assertEqual(runtime_skill.resolve(), destination.resolve())
        status, target = cli.describe_entry(runtime_skill, cli.discover_skills()[0])
        self.assertEqual(status, cli.STATUS_MANAGED)
        self.assertEqual(target, destination.resolve())

    def test_adopt_project_scope(self):
        target = self.repo / ".agents" / "skills"
        runtime_skill = self.make_runtime_skill("project-skill", target=target)

        self.assertEqual(
            cli.adopt("project-skill", project=str(self.repo)),
            0,
        )

        destination = self.library / "imported" / "project-skill"
        self.assertTrue(runtime_skill.is_symlink())
        self.assertEqual(runtime_skill.resolve(), destination.resolve())

    def test_adopt_external_symlink_preserves_external_source(self):
        external = self.root / "external-source"
        external.mkdir()
        (external / "SKILL.md").write_text(
            "---\nname: linked-skill\ndescription: external\n---\n"
        )
        (external / "payload.txt").write_text("external payload")
        self.codex_user.mkdir()
        runtime_link = self.codex_user / "linked-skill"
        os.symlink(external, runtime_link)

        self.assertEqual(cli.adopt("linked-skill", user=True), 0)

        destination = self.library / "imported" / "linked-skill"
        self.assertTrue(external.is_dir())
        self.assertEqual((external / "payload.txt").read_text(), "external payload")
        self.assertTrue(runtime_link.is_symlink())
        self.assertEqual(runtime_link.resolve(), destination.resolve())
        self.assertNotEqual(runtime_link.resolve(), external.resolve())

    def test_adopt_dry_run_does_not_change_filesystem(self):
        runtime_skill = self.make_runtime_skill("dry-run")

        self.assertEqual(cli.adopt("dry-run", user=True, dry_run=True), 0)

        self.assertTrue(runtime_skill.is_dir())
        self.assertFalse(runtime_skill.is_symlink())
        self.assertFalse((self.library / "imported" / "dry-run").exists())

    def test_adopt_rejects_non_unmanaged_entry(self):
        canonical = self.library / "existing" / "managed"
        canonical.mkdir(parents=True)
        (canonical / "SKILL.md").write_text(
            "---\nname: managed\ndescription: managed\n---\n"
        )
        cli.mount("managed", user=True)

        with self.assertRaises(cli.LibrarianError):
            cli.adopt("managed", user=True)

        runtime_link = self.codex_user / "managed"
        self.assertTrue(runtime_link.is_symlink())
        self.assertEqual(runtime_link.resolve(), canonical.resolve())

    def test_adopt_rejects_frontmatter_name_mismatch(self):
        runtime_skill = self.make_runtime_skill("foo", frontmatter_name="bar")

        with self.assertRaises(cli.LibrarianError):
            cli.adopt("foo", user=True)

        self.assertTrue(runtime_skill.is_dir())
        self.assertFalse(runtime_skill.is_symlink())
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_adopt_rejects_multiple_runtimes(self):
        self.make_runtime_skill("foo")
        with self.assertRaises(cli.LibrarianError):
            cli.adopt("foo", user=True, agents=["all"])

    def test_adopt_requires_library_choice_when_multiple_exist(self):
        second = self.root / "second-library"
        second.mkdir()
        cfg = self.read_cfg()
        cfg["libraries"].append(str(second))
        self.write_cfg(cfg)
        self.make_runtime_skill("foo")

        with self.assertRaises(cli.LibrarianError):
            cli.adopt("foo", user=True)

        self.assertEqual(
            cli.adopt("foo", user=True, library=str(second), category="database_skills"),
            0,
        )
        self.assertTrue((second / "database_skills" / "foo").is_dir())

    def test_adopt_rejects_category_escape(self):
        runtime_skill = self.make_runtime_skill("foo")
        with self.assertRaises(cli.LibrarianError):
            cli.adopt("foo", user=True, category="../outside")
        self.assertTrue(runtime_skill.is_dir())
        self.assertFalse((self.root / "outside" / "foo").exists())

    def test_adopt_rejects_internal_link_outside_skill_tree(self):
        runtime_skill = self.make_runtime_skill("foo")
        outside = self.root / "outside.txt"
        outside.write_text("outside")
        os.symlink(outside, runtime_skill / "outside-link")

        with self.assertRaises(cli.LibrarianError):
            cli.adopt("foo", user=True)

        self.assertTrue(runtime_skill.is_dir())
        self.assertFalse((self.library / "imported" / "foo").exists())

    def test_adopt_rolls_back_when_runtime_link_creation_fails(self):
        runtime_skill = self.make_runtime_skill("foo")

        with mock.patch.object(cli, "make_link", side_effect=OSError("simulated link failure")):
            with self.assertRaises(cli.LibrarianError):
                cli.adopt("foo", user=True)

        self.assertTrue(runtime_skill.is_dir())
        self.assertFalse(runtime_skill.is_symlink())
        self.assertEqual((runtime_skill / "payload.txt").read_text(), "payload")
        self.assertFalse((self.library / "imported" / "foo").exists())
        backups = list(self.codex_user.glob(".skill-librarian-adopt-backup-*"))
        self.assertEqual(backups, [])


if __name__ == "__main__":
    unittest.main()
