import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import importlib.util

MODULE_PATH = Path(__file__).resolve().parents[1] / "skill-librarian" / "scripts" / "skill_source.py"
spec = importlib.util.spec_from_file_location("skill_source", MODULE_PATH)
source_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source_mod)


class SkillSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def make_skill(self, root, name, folder=None):
        skill = Path(root) / (folder or name)
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: test\n---\n",
            encoding="utf-8",
        )
        (skill / "payload.txt").write_text("payload", encoding="utf-8")
        return skill

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    def init_repo(self, repo):
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
        self.git(repo, "config", "user.email", "test@example.com")
        self.git(repo, "config", "user.name", "Test User")

    def commit_all(self, repo, message="initial"):
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", message)
        return self.git(repo, "rev-parse", "HEAD").stdout.strip()

    def test_local_source_find_skill_and_plain_local_provenance(self):
        source_root = self.root / "source"
        skill = self.make_skill(source_root, "foo", folder="group/foo")

        with source_mod.acquire_source(str(source_root)) as acquired:
            selected = source_mod.find_skill(acquired, "foo")
            self.assertEqual(selected, skill.resolve())
            metadata = source_mod.build_provenance(
                acquired,
                selected,
                "foo",
                imported_at="2026-09-16T00:00:00Z",
            )

        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["type"], "local")
        self.assertIsNone(metadata["source"])
        self.assertEqual(metadata["source_path"], "group/foo")
        self.assertEqual(metadata["skill"], "foo")
        self.assertEqual(metadata["acquired_via"], "local")
        self.assertNotIn(str(self.root), json.dumps(metadata))

    def test_local_git_source_records_remote_revision_and_dirty_state(self):
        repo = self.root / "repo"
        self.init_repo(repo)
        skill = self.make_skill(repo, "foo", folder="skills/foo")
        revision = self.commit_all(repo)
        self.git(repo, "remote", "add", "origin", "https://token@example.com/acme/skills.git?secret=1")
        (skill / "dirty.txt").write_text("dirty", encoding="utf-8")

        with source_mod.acquire_source(str(repo)) as acquired:
            selected = source_mod.find_skill(acquired, "foo")
            metadata = source_mod.build_provenance(
                acquired,
                selected,
                "foo",
                imported_at="2026-09-16T00:00:00Z",
            )

        self.assertEqual(metadata["type"], "git")
        self.assertEqual(metadata["source"], "https://example.com/acme/skills.git")
        self.assertEqual(metadata["source_path"], "skills/foo")
        self.assertEqual(metadata["revision"], revision)
        self.assertTrue(metadata["dirty"])
        self.assertEqual(metadata["acquired_via"], "local")
        self.assertNotIn("token", json.dumps(metadata))
        self.assertNotIn("secret=1", json.dumps(metadata))

    def test_remote_file_git_source_clones_without_persisting_machine_path(self):
        repo = self.root / "remote-repo"
        self.init_repo(repo)
        skill = self.make_skill(repo, "foo", folder="skills/foo")
        revision = self.commit_all(repo)
        source = repo.resolve().as_uri()

        with source_mod.acquire_source(source) as acquired:
            selected = source_mod.find_skill(acquired, "foo")
            self.assertNotEqual(selected, skill.resolve())
            self.assertEqual((selected / "payload.txt").read_text(), "payload")
            metadata = source_mod.build_provenance(
                acquired,
                selected,
                "foo",
                imported_at="2026-09-16T00:00:00Z",
            )

        self.assertEqual(metadata["type"], "git")
        self.assertIsNone(metadata["source"])
        self.assertEqual(metadata["source_path"], "skills/foo")
        self.assertEqual(metadata["revision"], revision)
        self.assertFalse(metadata["dirty"])
        self.assertEqual(metadata["acquired_via"], "git")
        self.assertNotIn(str(repo.resolve()), json.dumps(metadata))

    def test_remote_ref_checkout_uses_requested_revision(self):
        repo = self.root / "remote-ref"
        self.init_repo(repo)
        self.make_skill(repo, "foo", folder="skills/foo")
        first = self.commit_all(repo, "first")
        self.git(repo, "tag", "v1")
        (repo / "skills" / "foo" / "payload.txt").write_text("new", encoding="utf-8")
        self.commit_all(repo, "second")

        with source_mod.acquire_source(repo.resolve().as_uri(), ref="v1") as acquired:
            selected = source_mod.find_skill(acquired, "foo")
            metadata = source_mod.build_provenance(
                acquired,
                selected,
                "foo",
                imported_at="2026-09-16T00:00:00Z",
            )
            self.assertEqual((selected / "payload.txt").read_text(), "payload")

        self.assertEqual(metadata["ref"], "v1")
        self.assertEqual(metadata["revision"], first)

    def test_git_error_redacts_credential_bearing_source(self):
        raw = "https://token@example.com/acme/private.git?secret=1"
        failure = subprocess.CompletedProcess(
            ["git", "clone"],
            128,
            stdout="",
            stderr=f"fatal: unable to access '{raw}': denied",
        )
        with mock.patch.object(source_mod.subprocess, "run", return_value=failure):
            with self.assertRaises(source_mod.SourceError) as ctx:
                source_mod._run_git(
                    ["clone", "--quiet", raw, "/tmp/dest"],
                    sensitive_values=(raw,),
                )
        message = str(ctx.exception)
        self.assertNotIn("token", message)
        self.assertNotIn("secret=1", message)
        self.assertIn("https://example.com/acme/private.git", message)

    def test_find_skill_respects_skill_boundaries(self):
        source_root = self.root / "source-boundary"
        outer = self.make_skill(source_root, "outer", folder="outer")
        self.make_skill(outer, "foo", folder="nested/foo")

        with source_mod.acquire_source(str(source_root)) as acquired:
            with self.assertRaises(source_mod.SourceError):
                source_mod.find_skill(acquired, "foo")

    def test_find_skill_rejects_ambiguous_matches(self):
        source_root = self.root / "ambiguous"
        self.make_skill(source_root, "foo", folder="a/foo-a")
        self.make_skill(source_root, "foo", folder="b/foo-b")

        with source_mod.acquire_source(str(source_root)) as acquired:
            with self.assertRaises(source_mod.SourceError):
                source_mod.find_skill(acquired, "foo")

    def test_github_tree_url_extracts_repo_ref_and_path_hint(self):
        clone_url, ref, hint = source_mod._parse_remote_source(
            "https://github.com/acme/skills/tree/main/skills/foo",
            ref=None,
        )
        self.assertEqual(clone_url, "https://github.com/acme/skills.git")
        self.assertEqual(ref, "main")
        self.assertEqual(hint, Path("skills/foo"))

    def test_github_tree_url_rejects_conflicting_ref(self):
        with self.assertRaises(source_mod.SourceError):
            source_mod._parse_remote_source(
                "https://github.com/acme/skills/tree/main/skills/foo",
                ref="other",
            )

    def test_hinted_skill_must_match_requested_frontmatter_name(self):
        source_root = self.root / "hinted"
        wrong = self.make_skill(source_root, "bar", folder="skills/bar")
        acquired = source_mod.AcquiredSource(
            root=source_root.resolve(),
            requested_source="example",
            source_type="git",
            recorded_source="https://github.com/acme/skills.git",
            requested_ref="main",
            revision="abc",
            dirty=False,
            acquired_via="git",
            path_hint=Path("skills/bar"),
        )
        self.assertTrue((wrong / "SKILL.md").is_file())
        with self.assertRaises(source_mod.SourceError):
            source_mod.find_skill(acquired, "foo")

    def test_write_provenance_is_stable_json(self):
        skill = self.root / "skill"
        skill.mkdir()
        metadata = {
            "schema_version": 1,
            "type": "git",
            "source": "https://github.com/acme/skills.git",
            "source_path": "skills/foo",
            "ref": "main",
            "revision": "abc123",
            "dirty": False,
            "skill": "foo",
            "acquired_via": "git",
            "imported_at": "2026-09-16T00:00:00Z",
        }
        path = source_mod.write_provenance(skill, metadata)
        self.assertEqual(path.name, source_mod.PROVENANCE_FILENAME)
        self.assertEqual(json.loads(path.read_text()), metadata)
        self.assertTrue(path.read_text().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
