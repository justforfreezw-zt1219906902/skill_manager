import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "skill-librarian"
    / "scripts"
    / "project_inventory.py"
)
spec = importlib.util.spec_from_file_location("project_inventory_cli", MODULE_PATH)
inventory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inventory)


class ProjectInventoryTests(unittest.TestCase):
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
        self.group = self.library / "coding_skills"
        self.group.mkdir()

        self.shared_skill = self.group / "shared-skill"
        self.shared_skill.mkdir()
        (self.shared_skill / "SKILL.md").write_text(
            "---\nname: shared-skill\ndescription: test\n---\n",
            encoding="utf-8",
        )

        self.project_skill = self.group / "project-only"
        self.project_skill.mkdir()
        (self.project_skill / "SKILL.md").write_text(
            "---\nname: project-only\ndescription: test\n---\n",
            encoding="utf-8",
        )

        self.repo = self.root / "project-a"
        self.repo.mkdir()
        subprocess.run(["git", "init", str(self.repo)], check=True, capture_output=True)

        self.repo_b = self.root / "project-b"
        self.repo_b.mkdir()
        subprocess.run(["git", "init", str(self.repo_b)], check=True, capture_output=True)

        (self.framework / "deploy.json").write_text(
            json.dumps(
                {
                    "libraries": [str(self.library)],
                    "user_target": str(self.root / "user-skills"),
                    "runtimes": {
                        "codex": {"enabled": True},
                        "claude-code": {
                            "enabled": True,
                            "user_target": str(self.root / "claude-user-skills"),
                        },
                    },
                }
            ),
            encoding="utf-8",
        )

        self.registry = self.root / "config" / "projects.json"
        self.env_patch = mock.patch.dict(
            os.environ,
            {"SKILL_LIBRARIAN_PROJECTS_FILE": str(self.registry)},
        )
        self.framework_patch = mock.patch.object(
            inventory.core,
            "FRAMEWORK_ROOT",
            self.framework,
        )
        self.skill_dir_patch = mock.patch.object(
            inventory.core,
            "SKILL_DIR",
            self.skill_dir,
        )
        self.env_patch.start()
        self.framework_patch.start()
        self.skill_dir_patch.start()

    def tearDown(self):
        self.skill_dir_patch.stop()
        self.framework_patch.stop()
        self.env_patch.stop()
        self.tmp.cleanup()

    def test_project_add_list_remove_round_trip(self):
        self.assertEqual(inventory.add_project(str(self.repo)), 0)
        self.assertEqual(inventory.read_registered_projects(), [self.repo.resolve()])

        payload = json.loads(self.registry.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["projects"], [str(self.repo.resolve())])

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(inventory.list_projects(json_output=True), 0)
        listed = json.loads(out.getvalue())
        self.assertEqual(listed["projects"][0]["status"], "OK")
        self.assertEqual(listed["projects"][0]["project"], str(self.repo.resolve()))

        self.assertEqual(inventory.remove_project(str(self.repo)), 0)
        self.assertEqual(inventory.read_registered_projects(), [])

    def test_project_add_normalizes_subdirectory_to_git_root(self):
        nested = self.repo / "src" / "feature"
        nested.mkdir(parents=True)
        self.assertEqual(inventory.add_project(str(nested)), 0)
        self.assertEqual(inventory.read_registered_projects(), [self.repo.resolve()])

    def test_global_inventory_combines_user_and_registered_project_scopes(self):
        self.assertEqual(inventory.core.mount("shared-skill", user=True), 0)
        self.assertEqual(
            inventory.core.mount("project-only", project=str(self.repo)),
            0,
        )
        self.assertEqual(inventory.add_project(str(self.repo)), 0)

        rows, warnings = inventory.collect_global_rows(agents=["codex"])
        self.assertEqual(warnings, [])

        by_name = {row["name"]: row for row in rows}
        self.assertEqual(by_name["shared-skill"]["scope"], "user")
        self.assertIsNone(by_name["shared-skill"]["project"])
        self.assertEqual(by_name["shared-skill"]["status"], inventory.core.STATUS_MANAGED)

        self.assertEqual(by_name["project-only"]["scope"], "project")
        self.assertEqual(by_name["project-only"]["project"], str(self.repo.resolve()))
        self.assertEqual(by_name["project-only"]["status"], inventory.core.STATUS_MANAGED)

    def test_global_inventory_supports_all_enabled_runtimes(self):
        self.assertEqual(
            inventory.core.mount("shared-skill", user=True, agents=["all"]),
            0,
        )
        rows, warnings = inventory.collect_global_rows(agents=["all"])
        self.assertEqual(warnings, [])
        self.assertEqual({row["runtime"] for row in rows}, {"codex", "claude-code"})
        self.assertEqual({row["scope"] for row in rows}, {"user"})

    def test_global_inventory_warns_and_skips_missing_registered_project(self):
        self.assertEqual(inventory.add_project(str(self.repo_b)), 0)
        shutil.rmtree(self.repo_b)

        rows, warnings = inventory.collect_global_rows(agents=["codex"])
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("registered project is missing", warnings[0])

    def test_global_json_includes_project_field_and_registry(self):
        self.assertEqual(
            inventory.core.mount("project-only", project=str(self.repo)),
            0,
        )
        self.assertEqual(inventory.add_project(str(self.repo)), 0)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(inventory.list_global(agents=["codex"], json_output=True), 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["registry"], str(self.registry.resolve()))
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(len(payload["mounts"]), 1)
        self.assertEqual(payload["mounts"][0]["project"], str(self.repo.resolve()))
        self.assertEqual(payload["mounts"][0]["scope"], "project")


if __name__ == "__main__":
    unittest.main()
