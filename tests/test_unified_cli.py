import importlib.util
from pathlib import Path
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parents[1] / "skill-librarian" / "scripts" / "skill_librarian.py"
spec = importlib.util.spec_from_file_location("skill_librarian_unified", MODULE_PATH)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class UnifiedCliTests(unittest.TestCase):
    def test_list_global_routes_to_inventory(self):
        inventory = mock.Mock()
        inventory.main.return_value = 17
        with mock.patch.object(cli, "_load_inventory_module", return_value=inventory):
            self.assertEqual(cli.main(["list", "--global"]), 17)
        inventory.main.assert_called_once_with(["list", "--global"])

    def test_projects_routes_to_inventory(self):
        inventory = mock.Mock()
        inventory.main.return_value = 18
        with mock.patch.object(cli, "_load_inventory_module", return_value=inventory):
            self.assertEqual(cli.main(["projects"]), 18)
        inventory.main.assert_called_once_with(["projects"])

    def test_vendor_routes_to_vendor_workflow(self):
        vendor = mock.Mock()
        vendor.main.return_value = 19
        with mock.patch.object(cli, "_load_vendor_module", return_value=vendor):
            self.assertEqual(
                cli.main(["vendor", "demo-skill", "--project", "/tmp/repo"]),
                19,
            )
        vendor.main.assert_called_once()
        args, kwargs = vendor.main.call_args
        self.assertEqual(args[0], ["demo-skill", "--project", "/tmp/repo"])
        self.assertIs(kwargs["control"], cli)
        self.assertIs(kwargs["register_project"], cli._register_project)

    def test_normal_commands_keep_core_behavior(self):
        with mock.patch.object(cli, "_CORE_MAIN", return_value=0) as core_main:
            self.assertEqual(cli.main(["list", "--user"]), 0)
        core_main.assert_called_once_with(["list", "--user"])

    def test_successful_project_mount_registers_project(self):
        inventory = mock.Mock()
        with mock.patch.object(cli, "_CORE_MAIN", return_value=0), mock.patch.object(
            cli, "_load_inventory_module", return_value=inventory
        ):
            self.assertEqual(cli.main(["mount", "demo-skill", "--project", "/tmp/repo"]), 0)
        inventory.add_project.assert_called_once_with("/tmp/repo")

    def test_default_project_mount_registers_current_repo(self):
        inventory = mock.Mock()
        with mock.patch.object(cli, "_CORE_MAIN", return_value=0), mock.patch.object(
            cli, "_load_inventory_module", return_value=inventory
        ):
            self.assertEqual(cli.main(["mount", "demo-skill"]), 0)
        inventory.add_project.assert_called_once_with(".")

    def test_user_mount_and_dry_run_do_not_register(self):
        with mock.patch.object(cli, "_CORE_MAIN", return_value=0), mock.patch.object(
            cli, "_load_inventory_module"
        ) as load_inventory:
            self.assertEqual(cli.main(["mount", "demo-skill", "--user"]), 0)
            self.assertEqual(cli.main(["mount", "demo-skill", "--dry-run"]), 0)
        load_inventory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
