import contextlib
from dataclasses import replace
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skill-librarian" / "scripts"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


control = load(SCRIPTS / "skill_librarian.py", "lifecycle_test_control")
life = load(SCRIPTS / "skill_lifecycle.py", "lifecycle_test_module")
REMOTE = "https://upstream.example/skills.git"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # macOS /var is a symlink to /private/var; injections must compare the
        # same physical paths that canonical discovery returns in production.
        self.root = Path(self.tmp.name).resolve()
        self.framework = self.root / "framework"
        self.skill_dir = self.framework / "skill-librarian"
        self.skill_dir.mkdir(parents=True)
        (self.skill_dir / "SKILL.md").write_text("---\nname: skill-librarian\ndescription: test\n---\n")
        self.library = self.root / "library"
        self.library.mkdir()
        (self.framework / "deploy.json").write_text(json.dumps({
            "libraries": [str(self.library)], "ignored_directories": ["retire_skills"],
        }))
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(control, "FRAMEWORK_ROOT", self.framework).start()
        mock.patch.object(control, "SKILL_DIR", self.skill_dir).start()
        self.repo = self.root / "remote"
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True, capture_output=True)
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Lifecycle Test")
        for name in ("foo", "bar"):
            path = self.repo / "skills" / name
            path.mkdir(parents=True)
            (path / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test\n---\n")
            (path / "references").mkdir()
            (path / "references" / "rules.md").write_text("baseline\n")
            (path / "scripts").mkdir()
            script = path / "scripts" / "helper.py"
            script.write_text("raise RuntimeError('upstream scripts must never be executed')\n")
            script.chmod(0o755)
        self.baseline = self.commit("baseline")
        self.git("tag", "v1")
        self.canonical = self.library / "coding" / "foo"
        self.canonical.parent.mkdir(parents=True)
        for name in ("foo", "bar"):
            shutil.copytree(self.repo / "skills" / name, self.canonical.parent / name)
            self.metadata(name)
        original_acquire = life.source.acquire_source

        @contextlib.contextmanager
        def offline_acquire(url, ref=None):
            if url != REMOTE:
                raise life.source.SourceError("unreachable test source")
            with original_acquire(self.repo.as_uri(), ref=ref) as item:
                yield replace(item, requested_source=url, recorded_source=url)

        self.acquire = mock.patch.object(life.source, "acquire_source", side_effect=offline_acquire).start()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)
        return self.git("rev-parse", "HEAD")

    def metadata(self, name="foo", **extra):
        value = {
            "schema_version": 1, "type": "git", "source": REMOTE,
            "source_path": f"skills/{name}", "skill": name, "ref": "main",
            "revision": self.baseline, "dirty": False, "acquired_via": "git",
            "imported_at": "2026-09-01T00:00:00Z",
        }
        value.update(extra)
        (self.library / "coding" / name / ".skill-source.json").write_text(json.dumps(value))
        return value

    def change_upstream(self, name="foo", content="new upstream\n"):
        (self.repo / "skills" / name / "references" / "rules.md").write_text(content)
        return self.commit("change " + name)

    def inspect(self, name="foo", **kwargs):
        with life.Snapshots() as snapshots:
            return life.inspect(name, control=control, snapshots=snapshots, **kwargs)

    def update(self, name="foo", **kwargs):
        with life.Snapshots() as snapshots:
            return life.update(name, control=control, snapshots=snapshots, **kwargs)

    def migrate(self, name="foo", **kwargs):
        with life.Snapshots() as snapshots:
            return life.migrate(name, remote=REMOTE, control=control, snapshots=snapshots, ref="main", **kwargs)

    def cli(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            rc = life.main([*args, "--json"], control=control)
        return rc, json.loads(output.getvalue())

    def test_status_all_is_sorted_and_reuses_shared_source_snapshots(self):
        rc, result = self.cli(["upstream", "status", "--all"])
        self.assertEqual(rc, 0)
        self.assertEqual([r["skill"] for r in result["skills"]], ["bar", "foo", "skill-librarian"])
        self.assertEqual(result["summary"], {"SAME": 2, "UNTRACKABLE": 1})
        self.assertEqual(self.acquire.call_count, 2)

    def test_status_all_isolates_corrupt_metadata_and_continues(self):
        (self.library / "coding" / "bar" / ".skill-source.json").write_text("not json")
        rc, result = self.cli(["upstream", "status", "--all"])
        self.assertEqual(rc, 2)
        rows = {r["skill"]: r for r in result["skills"]}
        self.assertEqual(rows["bar"]["status"], "ERROR")
        self.assertEqual(rows["foo"]["status"], "SAME")

    def test_network_failure_is_cached_and_reported_per_skill(self):
        self.acquire.side_effect = life.source.SourceError("offline")
        rc, result = self.cli(["upstream", "status", "--all"])
        self.assertEqual(rc, 2)
        self.assertEqual(result["summary"], {"ERROR": 2, "UNTRACKABLE": 1})
        self.assertEqual(self.acquire.call_count, 1)

    def test_status_all_does_not_discover_retired_skills(self):
        retired = self.library / "retire_skills" / "old"
        retired.mkdir(parents=True)
        (retired / "SKILL.md").write_text("---\nname: old\n---\n")
        rc, result = self.cli(["upstream", "status", "--all"])
        self.assertEqual(rc, 0)
        self.assertNotIn("old", [r["skill"] for r in result["skills"]])

    def test_duplicate_canonical_names_are_not_silently_selected(self):
        duplicate = self.library / "other" / "foo"
        duplicate.parent.mkdir()
        shutil.copytree(self.canonical, duplicate)
        with self.assertRaises(life.LifecycleError):
            self.update()

    def test_reference_only_changes_are_detected(self):
        self.change_upstream()
        self.assertEqual(self.inspect()["status"], "OUTDATED")

    def test_local_and_diverged_states_are_protected(self):
        local = self.canonical / "references" / "rules.md"
        local.write_text("my customization\n")
        self.assertEqual(self.update()["status"], "LOCAL_MODIFIED")
        self.change_upstream()
        result = self.update()
        self.assertEqual(result["status"], "DIVERGED")
        self.assertEqual(result["action"], "BLOCKED")
        self.assertEqual(local.read_text(), "my customization\n")

    def test_missing_provenance_is_never_guessed(self):
        (self.canonical / ".skill-source.json").unlink()
        result = self.update()
        self.assertEqual(result["status"], "UNTRACKABLE")
        self.assertEqual(result["action"], "BLOCKED")
        self.assertEqual(self.acquire.call_count, 0)

    def test_missing_upstream_never_deletes_local_skill(self):
        shutil.rmtree(self.repo / "skills" / "foo")
        self.commit("remove upstream foo")
        result = self.update()
        self.assertEqual(result["action"], "BLOCKED")
        self.assertFalse(result["upstream_present"])
        self.assertTrue(self.canonical.is_dir())

    def test_update_dry_run_leaves_content_metadata_and_directories_unchanged(self):
        self.change_upstream()
        before = sorted(p.relative_to(self.library).as_posix() for p in self.library.rglob("*"))
        raw = (self.canonical / ".skill-source.json").read_bytes()
        result = self.update(dry_run=True)
        self.assertEqual(result["action"], "WOULD_UPDATE")
        self.assertEqual((self.canonical / ".skill-source.json").read_bytes(), raw)
        self.assertEqual(before, sorted(p.relative_to(self.library).as_posix() for p in self.library.rglob("*")))
        self.assertEqual(self.inspect()["status"], "OUTDATED")

    def test_clean_update_keeps_backup_and_existing_runtime_links(self):
        revision = self.change_upstream()
        runtime = self.root / "runtime"
        runtime.mkdir()
        (runtime / "foo").symlink_to(self.canonical, target_is_directory=True)
        original_link = os.readlink(runtime / "foo")
        result = self.update(expected_revision=revision)
        self.assertEqual(result["action"], "UPDATED")
        backup = Path(result["backup"])
        self.assertEqual((backup / "references" / "rules.md").read_text(), "baseline\n")
        self.assertEqual(os.readlink(runtime / "foo"), original_link)
        self.assertEqual((runtime / "foo" / "references" / "rules.md").read_text(), "new upstream\n")
        metadata = json.loads((self.canonical / ".skill-source.json").read_text())
        self.assertEqual(metadata["revision"], revision)
        self.assertEqual(metadata["imported_at"], "2026-09-01T00:00:00Z")
        self.assertEqual(self.inspect()["status"], "SAME")
        self.assertEqual(set(control.discover_skills()[0]), {"foo", "bar", "skill-librarian"})

    def test_pinned_ref_does_not_silently_follow_main(self):
        self.metadata(ref="v1")
        self.change_upstream()
        self.assertEqual(self.inspect()["status"], "SAME")
        self.assertEqual(self.update()["action"], "UNCHANGED")
        self.assertEqual(self.update(ref="main")["action"], "UPDATED")
        self.assertEqual(json.loads((self.canonical / ".skill-source.json").read_text())["ref"], "main")

    def test_expected_revision_guard_blocks_moving_source(self):
        self.change_upstream()
        with self.assertRaisesRegex(life.LifecycleError, "expected-revision"):
            self.update(expected_revision=self.baseline)
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")

    def test_same_content_can_refresh_stale_provenance_safely(self):
        revision = self.change_upstream()
        (self.canonical / "references" / "rules.md").write_text("new upstream\n")
        result = self.update()
        self.assertEqual(result["action"], "REFRESHED")
        self.assertEqual(json.loads((self.canonical / ".skill-source.json").read_text())["revision"], revision)
        self.assertFalse(self.inspect()["local_modified"])

    def test_update_rolls_back_after_final_verification_failure(self):
        raw = (self.canonical / ".skill-source.json").read_bytes()
        self.change_upstream()
        with mock.patch.object(life, "_verify_published", side_effect=RuntimeError("injected")):
            with self.assertRaisesRegex(life.LifecycleError, "restored"):
                self.update()
        self.assertEqual((self.canonical / ".skill-source.json").read_bytes(), raw)
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")

    def test_update_rolls_back_after_publication_rename_failure(self):
        self.change_upstream()
        original_replace = os.replace
        failed = [False]

        def fail_once(src, dst):
            if Path(dst) == self.canonical and not failed[0]:
                failed[0] = True
                raise OSError("injected rename failure")
            return original_replace(src, dst)

        with mock.patch.object(life.os, "replace", side_effect=fail_once):
            with self.assertRaises(life.LifecycleError):
                self.update()
        self.assertTrue(failed[0], "The publication failure must actually be injected")
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")

    def test_keyboard_interrupt_after_publication_restores_original(self):
        self.change_upstream()
        with mock.patch.object(life, "_verify_published", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.update()
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")
        self.assertFalse((self.canonical.parent / ".skill-librarian-upstream-foo.lock").exists())

    def test_failed_rollback_keeps_recovery_backup_and_stage(self):
        self.change_upstream()
        original_replace = os.replace

        def refuse_restore(src, dst):
            if Path(src).name.startswith(".skill-librarian-backup-"):
                raise OSError("restore unavailable")
            return original_replace(src, dst)

        with mock.patch.object(life, "_verify_published", side_effect=RuntimeError("injected")), mock.patch.object(life.os, "replace", side_effect=refuse_restore):
            with self.assertRaisesRegex(life.LifecycleError, "manual recovery"):
                self.update()
        backups = list(self.canonical.parent.glob(".skill-librarian-backup-foo-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "references" / "rules.md").read_text(), "baseline\n")
        self.assertTrue(list(self.canonical.parent.glob(".skill-librarian-stage-*")))

    def test_late_user_edits_are_detected_before_swap(self):
        self.change_upstream()
        original_assert = life._assert_unchanged

        def edit_first(*args):
            (self.canonical / "references" / "rules.md").write_text("late user edit\n")
            return original_assert(*args)

        with mock.patch.object(life, "_assert_unchanged", side_effect=edit_first):
            with self.assertRaises(life.LifecycleError):
                self.update()
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "late user edit\n")

    def test_metadata_edit_after_inspection_is_not_overwritten(self):
        self.change_upstream()
        original_inspect = life.inspect

        def inspect_then_edit(*args, **kwargs):
            result = original_inspect(*args, **kwargs)
            self.metadata(imported_at="concurrent external edit")
            return result

        with mock.patch.object(life, "inspect", side_effect=inspect_then_edit):
            with self.assertRaisesRegex(life.LifecycleError, "Provenance changed"):
                self.update()
        metadata = json.loads((self.canonical / ".skill-source.json").read_text())
        self.assertEqual(metadata["imported_at"], "concurrent external edit")
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")

    def test_existing_lock_prevents_update(self):
        lock = self.canonical.parent / ".skill-librarian-upstream-foo.lock"
        lock.mkdir()
        with self.assertRaisesRegex(life.LifecycleError, "locked"):
            self.update()
        self.assertTrue(lock.exists())

    def test_update_rejects_secret_payload_before_modifying_canonical(self):
        (self.repo / "skills" / "foo" / ".env").write_text("TEST_SECRET=not-a-real-secret\n")
        self.commit("add unsafe environment file")
        with self.assertRaises(Exception):
            self.update()
        self.assertFalse((self.canonical / ".env").exists())

    def test_update_rejects_external_links(self):
        (self.repo / "skills" / "foo" / "escape").symlink_to("../../bar/references/rules.md")
        self.commit("unsafe external dependency")
        with self.assertRaises(Exception):
            self.update()
        self.assertFalse(os.path.lexists(self.canonical / "escape"))

    def test_update_preserves_safe_relative_internal_links(self):
        (self.repo / "skills" / "foo" / "rules-link").symlink_to("references/rules.md")
        self.commit("portable internal link")
        self.assertEqual(self.update()["action"], "UPDATED")
        self.assertEqual(os.readlink(self.canonical / "rules-link"), "references/rules.md")

    def test_nested_local_vcs_metadata_is_not_discarded(self):
        (self.canonical / ".git").mkdir()
        self.change_upstream()
        with self.assertRaisesRegex(life.LifecycleError, "VCS"):
            self.update()
        self.assertTrue((self.canonical / ".git").is_dir())

    def test_metadata_credentials_are_rejected_without_echoing_secret(self):
        self.metadata(source="https://user:TOP_SECRET@example.com/skills.git")
        rc, data = self.cli(["upstream", "status", "foo"])
        self.assertEqual(rc, 2)
        self.assertNotIn("TOP_SECRET", json.dumps(data))

    def test_github_credentials_are_rejected_before_url_normalization(self):
        for remote in ("https://user:TOP_SECRET@github.com/acme/skills", "https://github.com/acme/skills?token=TOP_SECRET"):
            with self.subTest(remote_kind="credential-bearing GitHub URL"):
                with self.assertRaises(life.LifecycleError) as caught:
                    life._remote(remote)
                self.assertNotIn("TOP_SECRET", str(caught.exception))
        self.assertEqual(self.acquire.call_count, 0)

    def test_git_timeout_does_not_echo_credential_bearing_command(self):
        secret_url = "https://user:TOP_SECRET@example.com/skills.git"
        with mock.patch.object(life.source.subprocess, "run", side_effect=subprocess.TimeoutExpired(["git", "clone", secret_url], 120)):
            with self.assertRaises(life.source.SourceError) as caught:
                life.source._run_git(["clone", secret_url], sensitive_values=(secret_url,))
        self.assertNotIn("TOP_SECRET", str(caught.exception))
        self.assertIn("120", str(caught.exception))

    def test_untrusted_source_path_is_rejected(self):
        for path in ("../foo", "/absolute", "..\\foo", "C:/foo"):
            with self.subTest(path=path):
                self.metadata(source_path=path)
                with self.assertRaises(Exception):
                    self.inspect()

    def test_migration_full_match_writes_metadata_only(self):
        (self.canonical / ".skill-source.json").unlink()
        before = life.upstream.fingerprint_skill_tree(self.canonical)
        result = self.migrate()
        self.assertEqual(result["action"], "MIGRATED")
        self.assertEqual(result["revision"], self.baseline)
        self.assertEqual(before, life.upstream.fingerprint_skill_tree(self.canonical))
        self.assertEqual(self.inspect()["status"], "SAME")

    def test_migration_dry_run_does_not_write_metadata(self):
        (self.canonical / ".skill-source.json").unlink()
        result = self.migrate(dry_run=True)
        self.assertEqual(result["action"], "WOULD_MIGRATE")
        self.assertFalse((self.canonical / ".skill-source.json").exists())

    def test_migration_does_not_relabel_mismatched_content_as_clean(self):
        (self.canonical / ".skill-source.json").unlink()
        (self.canonical / "references" / "rules.md").write_text("unknown user changes\n")
        self.assertEqual(self.migrate(history_limit=10)["action"], "NO_MATCH")
        self.assertFalse((self.canonical / ".skill-source.json").exists())

    def test_migration_can_match_explicit_historical_revision(self):
        (self.canonical / ".skill-source.json").unlink()
        self.change_upstream()
        result = self.migrate(baseline_ref=self.baseline)
        self.assertEqual(result["action"], "MIGRATED")
        self.assertEqual(self.inspect()["status"], "OUTDATED")

    def test_migration_history_scan_finds_full_payload_baseline(self):
        (self.canonical / ".skill-source.json").unlink()
        self.change_upstream()
        result = self.migrate(history_limit=10)
        self.assertEqual(result["action"], "MIGRATED")
        self.assertEqual(result["revision"], self.baseline)
        self.assertEqual(self.inspect()["status"], "OUTDATED")
        self.assertEqual(self.update()["action"], "UPDATED")

    def test_migration_refuses_existing_provenance(self):
        raw = (self.canonical / ".skill-source.json").read_bytes()
        self.assertEqual(self.migrate()["action"], "ALREADY_TRACKED")
        self.assertEqual((self.canonical / ".skill-source.json").read_bytes(), raw)

    def test_migration_refuses_symlink_provenance(self):
        target = self.root / "outside.json"
        target.write_text("outside")
        (self.canonical / ".skill-source.json").unlink()
        (self.canonical / ".skill-source.json").symlink_to(target)
        with self.assertRaises(Exception):
            self.migrate()
        self.assertEqual(target.read_text(), "outside")

    def test_migration_publication_does_not_overwrite_concurrent_metadata(self):
        (self.canonical / ".skill-source.json").unlink()
        target = self.canonical / ".skill-source.json"
        original_link = os.link

        def concurrent_write(src, dst):
            target.write_text("another writer's metadata")
            return original_link(src, dst)

        with mock.patch.object(life.os, "link", side_effect=concurrent_write):
            with self.assertRaises(FileExistsError):
                self.migrate()
        self.assertEqual(target.read_text(), "another writer's metadata")
        self.assertFalse(list(self.canonical.parent.glob(".skill-librarian-provenance-*")))

    def test_migration_failure_after_publication_cleans_only_own_metadata(self):
        (self.canonical / ".skill-source.json").unlink()
        original_assert = life._assert_unchanged
        calls = [0]

        def fail_second(*args):
            calls[0] += 1
            if calls[0] == 2:
                raise life.LifecycleError("injected validation failure")
            return original_assert(*args)

        with mock.patch.object(life, "_assert_unchanged", side_effect=fail_second):
            with self.assertRaises(life.LifecycleError):
                self.migrate()
        self.assertFalse((self.canonical / ".skill-source.json").exists())
        self.assertEqual((self.canonical / "references" / "rules.md").read_text(), "baseline\n")

    def test_manifest_migrates_individual_skills_and_reports_failures(self):
        for name in ("foo", "bar"):
            (self.library / "coding" / name / ".skill-source.json").unlink()
        (self.library / "coding" / "bar" / "references" / "rules.md").write_text("local only\n")
        manifest = self.root / "migration.json"
        manifest.write_text(json.dumps({"schema_version": 1, "skills": [
            {"skill": "bar", "source": REMOTE, "ref": "main"},
            {"skill": "foo", "source": REMOTE, "ref": "main"},
        ]}))
        rc, result = self.cli(["provenance", "migrate", "--manifest", str(manifest)])
        self.assertEqual(rc, 1)
        self.assertEqual(result["summary"], {"MIGRATED": 1, "NO_MATCH": 1})
        self.assertEqual(self.acquire.call_count, 1)

    def test_manifest_duplicates_fail_before_any_write(self):
        manifest = self.root / "migration.json"
        manifest.write_text(json.dumps({"schema_version": 1, "skills": [
            {"skill": "foo", "source": REMOTE}, {"skill": "foo", "source": REMOTE},
        ]}))
        with self.assertRaises(life.LifecycleError):
            life.read_manifest(manifest)

    def test_update_all_skips_unsafe_items_and_continues(self):
        (self.library / "coding" / "bar" / "references" / "rules.md").write_text("local customization\n")
        self.change_upstream()
        rc, result = self.cli(["upstream", "update", "--all"])
        self.assertEqual(rc, 1)
        self.assertEqual(result["summary"], {"UPDATED": 1, "BLOCKED": 2})
        self.assertEqual(self.inspect()["status"], "SAME")

    def test_canonical_update_does_not_update_project_copy(self):
        project_copy = self.root / "project" / ".agents" / "skills" / "foo"
        project_copy.parent.mkdir(parents=True)
        shutil.copytree(self.canonical, project_copy)
        self.change_upstream()
        self.update()
        self.assertEqual((project_copy / "references" / "rules.md").read_text(), "baseline\n")

    def test_legacy_status_engine_still_reads_migrated_provenance(self):
        (self.canonical / ".skill-source.json").unlink()
        self.migrate()
        result = life.upstream.status_skill("foo", control=control)
        self.assertEqual(result["status"], "SAME")

    def test_python_and_bin_entrypoints_expose_identical_status_and_help(self):
        scripts = self.skill_dir / "scripts"
        shutil.copytree(SCRIPTS, scripts, ignore=shutil.ignore_patterns("__pycache__"))
        (self.framework / "bin").mkdir()
        shutil.copy2(ROOT / "bin" / "skill-librarian", self.framework / "bin" / "skill-librarian")
        for name in ("foo", "bar"):
            (self.library / "coding" / name / ".skill-source.json").unlink()
        commands = (["upstream", "status", "--all", "--json"], ["upstream", "--help"], ["--help"])
        for command in commands:
            outputs = []
            for entry in (scripts / "skill_librarian.py", self.framework / "bin" / "skill-librarian"):
                run = subprocess.run([sys.executable, str(entry), *command], check=True, capture_output=True, text=True)
                outputs.append(run.stdout)
            self.assertEqual(outputs[0], outputs[1])
        self.assertIn("provenance migrate", outputs[0])


if __name__ == "__main__":
    unittest.main()
