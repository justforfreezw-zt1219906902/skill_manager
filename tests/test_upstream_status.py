import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CONTROL_PATH = ROOT / "skill-librarian" / "scripts" / "skill_librarian.py"
UPSTREAM_PATH = ROOT / "skill-librarian" / "scripts" / "skill_upstream.py"

control_spec = importlib.util.spec_from_file_location("skill_librarian_upstream_control", CONTROL_PATH)
control = importlib.util.module_from_spec(control_spec)
control_spec.loader.exec_module(control)

upstream_spec = importlib.util.spec_from_file_location("skill_upstream_status", UPSTREAM_PATH)
upstream = importlib.util.module_from_spec(upstream_spec)
upstream_spec.loader.exec_module(upstream)


class UpstreamStatusTests(unittest.TestCase):
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

        self.repo = self.root / "upstream"
        subprocess.run(
            ["git", "init", "-b", "main", str(self.repo)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test User")

        self.upstream_skill = self.repo / "skills" / "foo"
        self.upstream_skill.mkdir(parents=True)
        (self.upstream_skill / "SKILL.md").write_text(
            "---\nname: foo\ndescription: baseline\n---\n",
            encoding="utf-8",
        )
        (self.upstream_skill / "payload.txt").write_text("baseline\n", encoding="utf-8")
        self.baseline_revision = self.commit_all("baseline")

        self.canonical = self.library / "imported" / "foo"
        self.canonical.parent.mkdir(parents=True)
        shutil.copytree(self.upstream_skill, self.canonical)
        self.write_provenance()

    def tearDown(self):
        self.skill_dir_patch.stop()
        self.framework_patch.stop()
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def commit_all(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def provenance(self, **overrides):
        value = {
            "schema_version": 1,
            "type": "git",
            "source": self.repo.resolve().as_uri(),
            "source_path": "skills/foo",
            "ref": "main",
            "revision": self.baseline_revision,
            "dirty": False,
            "skill": "foo",
            "acquired_via": "git",
            "imported_at": "2026-09-16T00:00:00Z",
        }
        value.update(overrides)
        return value

    def write_provenance(self, **overrides):
        (self.canonical / ".skill-source.json").write_text(
            json.dumps(self.provenance(**overrides), indent=2) + "\n",
            encoding="utf-8",
        )

    def status(self):
        metadata = upstream.read_provenance(self.canonical, expected_skill="foo")
        return upstream.inspect_upstream_status(self.canonical, metadata)

    def test_read_provenance_missing_returns_none(self):
        (self.canonical / ".skill-source.json").unlink()
        self.assertIsNone(upstream.read_provenance(self.canonical, expected_skill="foo"))

    def test_read_provenance_rejects_skill_mismatch(self):
        self.write_provenance(skill="bar")
        with self.assertRaises(upstream.UpstreamError):
            upstream.read_provenance(self.canonical, expected_skill="foo")

    def test_read_provenance_rejects_escaping_source_path(self):
        self.write_provenance(source_path="../foo")
        with self.assertRaises(upstream.UpstreamError):
            upstream.read_provenance(self.canonical, expected_skill="foo")

    def test_fingerprint_ignores_generated_provenance(self):
        first = upstream.fingerprint_skill_tree(self.canonical)
        self.write_provenance(imported_at="2099-01-01T00:00:00Z")
        second = upstream.fingerprint_skill_tree(self.canonical)
        self.assertEqual(first, second)

    def test_same_when_local_and_upstream_match_imported_baseline(self):
        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_SAME)
        self.assertFalse(result["local_modified"])
        self.assertFalse(result["upstream_changed"])
        self.assertTrue(result["content_matches_upstream"])
        self.assertFalse(result["provenance_stale"])

    def test_outdated_when_upstream_skill_changes_only(self):
        (self.upstream_skill / "payload.txt").write_text("upstream v2\n", encoding="utf-8")
        latest = self.commit_all("upstream v2")

        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_OUTDATED)
        self.assertFalse(result["local_modified"])
        self.assertTrue(result["upstream_changed"])
        self.assertEqual(result["upstream_revision"], latest)

    def test_local_modified_when_canonical_changes_only(self):
        (self.canonical / "payload.txt").write_text("my local edit\n", encoding="utf-8")

        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_LOCAL_MODIFIED)
        self.assertTrue(result["local_modified"])
        self.assertFalse(result["upstream_changed"])

    def test_diverged_when_both_local_and_upstream_change_differently(self):
        (self.canonical / "payload.txt").write_text("my local edit\n", encoding="utf-8")
        (self.upstream_skill / "payload.txt").write_text("upstream v2\n", encoding="utf-8")
        self.commit_all("upstream v2")

        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_DIVERGED)
        self.assertTrue(result["local_modified"])
        self.assertTrue(result["upstream_changed"])
        self.assertFalse(result["content_matches_upstream"])

    def test_same_with_stale_provenance_when_local_already_matches_new_upstream(self):
        (self.upstream_skill / "payload.txt").write_text("upstream v2\n", encoding="utf-8")
        latest = self.commit_all("upstream v2")
        (self.canonical / "payload.txt").write_text("upstream v2\n", encoding="utf-8")

        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_SAME)
        self.assertTrue(result["local_modified"])
        self.assertTrue(result["upstream_changed"])
        self.assertTrue(result["content_matches_upstream"])
        self.assertTrue(result["provenance_stale"])
        self.assertEqual(result["upstream_revision"], latest)

    def test_outdated_when_recorded_source_path_is_removed_upstream(self):
        shutil.rmtree(self.upstream_skill)
        self.commit_all("remove foo")

        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_OUTDATED)
        self.assertFalse(result["upstream_present"])
        self.assertTrue(result["upstream_changed"])
        self.assertIn("absent", result["reason"])

    def test_untrackable_for_local_only_provenance(self):
        self.write_provenance(type="local", source=None, revision=None, ref=None)
        result = self.status()
        self.assertEqual(result["status"], upstream.STATUS_UNTRACKABLE)
        self.assertIn("local-only", result["reason"])

    def test_status_skill_reports_missing_provenance_as_untrackable(self):
        (self.canonical / ".skill-source.json").unlink()
        result = upstream.status_skill("foo", control=control)
        self.assertEqual(result["status"], upstream.STATUS_UNTRACKABLE)
        self.assertIn("missing", result["reason"])

    def test_status_cli_json(self):
        with mock.patch("builtins.print") as printed:
            rc = upstream.main(["status", "foo", "--json"], control=control)
        self.assertEqual(rc, 0)
        output = printed.call_args[0][0]
        parsed = json.loads(output)
        self.assertEqual(parsed["skill"], "foo")
        self.assertEqual(parsed["status"], upstream.STATUS_SAME)


if __name__ == "__main__":
    unittest.main()
