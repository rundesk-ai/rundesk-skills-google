"""Catalog structure and package contracts, verified entirely offline."""

import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SKILLS = {"google-analytics", "google-pagespeed-insights", "google-search-console"}
ALLOWED_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DECLARED_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class GoogleCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    def test_manifest_matches_complete_packages(self):
        self.assertEqual(1, self.manifest["schema"])
        self.assertEqual("rundesk-skills-google", self.manifest["name"])
        self.assertRegex(self.manifest["version"], r"^\d+\.\d+\.\d+$")
        declared = {entry["name"]: entry["path"] for entry in self.manifest["skills"]}
        self.assertEqual(EXPECTED_SKILLS, set(declared))
        packages = {path.name for path in (ROOT / "skills").iterdir() if path.is_dir()}
        self.assertEqual(EXPECTED_SKILLS, packages)
        for name, relative in declared.items():
            with self.subTest(skill=name):
                self.assertRegex(name, ALLOWED_NAME)
                package = ROOT / relative
                self.assertEqual(name, package.name)
                skill = (package / "SKILL.md").read_text(encoding="utf-8")
                self.assertRegex(skill, rf"(?m)^name: {re.escape(name)}$")
                frontmatter = skill.split("---", 2)[1]
                keys = [line.split(":", 1)[0] for line in frontmatter.splitlines()
                        if line and not line.startswith(" ")]
                self.assertEqual(["name", "description"], keys)
                self.assertIn("Use ", re.search(r"(?m)^description: (.+)$", frontmatter).group(1))
                self.assertLess(len(skill.splitlines()), 500)
                self.assertFalse((package / "README.md").exists())
                self.assertTrue((package / "references" / "cli.md").is_file())
                self.assertTrue((package / "scripts" / name).is_file())

    def test_readme_lists_exactly_the_manifest_skills(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        listed = set(re.findall(r"(?m)^- `([a-z0-9-]+)`", readme))
        declared = {entry["name"] for entry in self.manifest["skills"]}
        self.assertEqual(declared, listed)

    def test_packages_declare_rundesk_credentials(self):
        for entry in self.manifest["skills"]:
            with self.subTest(skill=entry["name"]):
                package = ROOT / entry["path"]
                declaration = json.loads((package / "rundesk.json").read_text(encoding="utf-8"))
                self.assertEqual(["needs"], list(declaration))
                self.assertTrue(declaration["needs"])
                prefix = entry["name"].upper().replace("-", "_")
                for name, reason in declaration["needs"].items():
                    self.assertRegex(name, DECLARED_NAME)
                    self.assertNotIn("__", name)
                    self.assertTrue(name.startswith(prefix))
                    self.assertIsInstance(reason, str)
                    self.assertGreater(len(reason), 40)
                    self.assertNotIn(name, reason)

    def test_launchers_and_scripts_are_executable(self):
        for entry in self.manifest["skills"]:
            scripts = ROOT / entry["path"] / "scripts"
            for path in scripts.rglob("*"):
                if path.is_file() and path.suffix != ".pyc" and "__pycache__" not in path.parts:
                    with self.subTest(script=path.relative_to(ROOT)):
                        self.assertTrue(os.access(path, os.X_OK))

    def test_help_requires_no_credentials(self):
        clean = {key: value for key, value in os.environ.items()
                 if not any(word in key for word in ("TOKEN", "SECRET", "CREDENTIAL"))}
        for entry in self.manifest["skills"]:
            command = ROOT / entry["path"] / "scripts" / entry["name"]
            with self.subTest(skill=entry["name"]):
                completed = subprocess.run(
                    [str(command), "--help"], capture_output=True, text=True, check=False, env=clean
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("usage:", (completed.stdout + completed.stderr).lower())

    def test_every_package_offline_suite_passes(self):
        for entry in self.manifest["skills"]:
            support = ROOT / entry["path"] / "scripts" / f"{entry['name']}.d"
            tests = list(support.glob("test-*.py"))
            with self.subTest(skill=entry["name"]):
                self.assertEqual(1, len(tests))
                completed = subprocess.run(
                    [sys.executable, str(tests[0]), "-q"],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)

    def test_runtime_files_stay_package_local(self):
        self.assertFalse((ROOT / "scripts").exists(), "shared runtime scripts are prohibited")
        self.assertFalse((ROOT / "lib").exists(), "shared runtime libraries are prohibited")
        self.assertEqual([], list(ROOT.glob("*.py")), "runtime Python belongs inside a package")

    def test_repository_has_no_committed_secret_or_owner_path(self):
        forbidden = (
            "/Users/", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
        )
        for path in (ROOT / "skills").rglob("*"):
            if path.is_file() and ".git" not in path.parts and path.suffix != ".pyc":
                with self.subTest(path=path.relative_to(ROOT)):
                    text = path.read_text(encoding="utf-8", errors="ignore")
                    self.assertFalse(any(value.lower() in text.lower() for value in forbidden))


if __name__ == "__main__":
    unittest.main()
