"""Catalog structure and package contracts, verified entirely offline."""

import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SKILLS = {"google-analytics", "google-merchant", "google-pagespeed-insights",
                   "google-search-console"}
ALLOWED_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DECLARED_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
CATALOG_GUIDE = "https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md"
AGENT_GUIDE_HEADINGS = tuple("""# AGENTS
## Purpose
## Before you work
## Repository layout
## Package and artifact contract
## Safety and approval gates
## Delegation
## Architecture and conventions
## Documentation duties
## Build, test, and run
## Pull requests and releases
## Definition of done""".splitlines())
README_HEADINGS = tuple("""# Rundesk Google Skills
## Skills
## Install
## Requirements
## Repository layout
## Development
## Creating a skill catalog
## Contributing
## Releases
## License""".splitlines())
PR_HEADINGS = tuple("""## Summary
## Scope and compatibility
## Critical risk
## Validation
## Repository gates
## Release
## Manual user path""".splitlines())
ISSUE_TEMPLATE_CONTRACTS = {
    "bug-report.md": (
        ("name: Bug report", "about: Report reproducible incorrect behavior",
         'title: "[Bug] "', 'labels: ""', 'assignees: ""'),
        ("## Problem", "## Reproduction", "## Expected behavior", "## Evidence",
         "## Environment", "## Scope and privacy"),
        "747da5c0682a73adc61c35407327fb174c648630e80278c275af4a4542da6caf",
    ),
    "change-proposal.md": (
        ("name: Change proposal",
         "about: Propose a skill, integration, command, or repository improvement",
         'title: "[Proposal] "', 'labels: ""', 'assignees: ""'),
        ("## Problem", "## Desired outcome", "## Users and value",
         "## Scope and compatibility", "## Alternatives", "## Validation"),
        "2fe6a1d651ce91af2c3d19e98eea150ca26f41ad9a1ed95a6466a692b73eb4d7",
    ),
}
AGENT_GUIDE_ANCHORS = {
    "runtime": ("Python 3.9", "standard library"),
    "offline boundary": ("offline test", "network"),
    "package isolation": ("Packages do not", "depend on sibling packages"),
    "secret redaction": ("commit or print credentials", "OAuth grants"),
    "bounded reads": ("Bound every read", "truncation"),
    "mutation confirmation": ("Preview every mutation", "exact confirmation input"),
    "resource ambiguity": ("guess a profile, Google identity, account, property, site",),
    "OAuth scope": ("minimum documented OAuth scope",),
    "validation commands": (
        "python3 -m unittest discover -s tests -v",
        "python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q",
        "skills/google-search-console/scripts/google-search-console --help",
        '(cd /tmp && "$repository_root/skills/google-search-console/scripts/google-search-console" --help)',
    ),
    "privacy evidence": ("inspect the complete diff and commit-visible artifacts",),
    "diff check": ("git diff --check",),
    "exact head": ("exact pull request head commit",),
}
README_ANCHORS = (
    "rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google",
    "rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google --confirm",
    "rundesk skills grant agent-name rundesk-skills-google/google-search-console",
    "rundesk skills configure rundesk-skills-google/google-search-console",
    "rundesk skills profiles rundesk-skills-google/google-search-console",
    ".github/ISSUE_TEMPLATE/bug-report.md",
    ".github/ISSUE_TEMPLATE/change-proposal.md",
    ".github/pull_request_template.md",
    "python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q",
    '(cd /tmp && "$repository_root/skills/google-search-console/scripts/google-search-console" --help)',
)
PR_CHECKLIST_ANCHORS = (
    "Every mutation remains a preview until the owner approves the exact target and effect and "
    "supplies the package's exact confirmation input.",
    "Required GitHub checks pass for the exact head commit.",
    "`git diff --check`",
    "Reads remain bounded by default and report truncation explicitly.",
    "No package imports, executes, or depends on a sibling package.",
    "Runtime code remains Python 3.9+ and standard-library only, unless the owner approved a dependency.",
    "Tests remain offline and replace OAuth and Google API boundaries with synthetic fixtures.",
    "Credential-free help, offline profiles, secret redaction, and unambiguous resource selection remain intact.",
    "The diff contains no credential, OAuth grant, customer identifier, private-project language, owner-specific path, or unrelated artifact.",
)


class GoogleCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))

    @staticmethod
    def markdown_headings(path):
        headings = []
        in_fence = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("```"):
                in_fence = not in_fence
            elif not in_fence and re.fullmatch(r"#{1,2} .+", line):
                headings.append(line)
        return tuple(headings)

    @staticmethod
    def shell_fences(text):
        return re.findall(r"(?ms)^```sh\n(.*?)^```$", text)

    def test_repository_guides_are_identical_and_structured(self):
        agents = ROOT / "AGENTS.md"
        claude = ROOT / "CLAUDE.md"
        self.assertTrue(claude.is_file())
        self.assertFalse(claude.is_symlink())
        self.assertEqual(agents.read_bytes(), claude.read_bytes())
        self.assertEqual(AGENT_GUIDE_HEADINGS, self.markdown_headings(agents))
        text = agents.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn(CATALOG_GUIDE, text)
        for purpose, anchors in AGENT_GUIDE_ANCHORS.items():
            with self.subTest(contract=purpose):
                for anchor in anchors:
                    self.assertIn(" ".join(anchor.split()), normalized)
        for fence in self.shell_fences(text):
            self.assertNotRegex(fence, r"<[^>\n]+>")

    def test_repository_templates_follow_the_contract(self):
        pull_request = ROOT / ".github" / "pull_request_template.md"
        self.assertEqual(PR_HEADINGS, self.markdown_headings(pull_request))
        pull_request_text = pull_request.read_text(encoding="utf-8")
        normalized_pull_request = " ".join(pull_request_text.split())
        for anchor in PR_CHECKLIST_ANCHORS:
            self.assertIn(" ".join(f"- [ ] {anchor}".split()), normalized_pull_request)
        issue_root = ROOT / ".github" / "ISSUE_TEMPLATE"
        self.assertEqual(
            set(ISSUE_TEMPLATE_CONTRACTS) | {"config.yml"},
            {path.name for path in issue_root.iterdir() if path.is_file()},
        )
        self.assertEqual(
            b"blank_issues_enabled: false\n",
            (issue_root / "config.yml").read_bytes(),
        )
        for filename, (frontmatter, headings, digest) in ISSUE_TEMPLATE_CONTRACTS.items():
            with self.subTest(template=filename):
                path = issue_root / filename
                raw = path.read_bytes()
                text = raw.decode("utf-8")
                self.assertEqual(["", *frontmatter], text.split("---", 2)[1].splitlines())
                self.assertEqual(headings, self.markdown_headings(path))
                self.assertEqual(digest, hashlib.sha256(raw).hexdigest())

    def test_readme_follows_the_catalog_contract(self):
        readme = ROOT / "README.md"
        self.assertEqual(README_HEADINGS, self.markdown_headings(readme))
        text = readme.read_text(encoding="utf-8")
        normalized = " ".join(text.split())
        self.assertIn(CATALOG_GUIDE, text)
        self.assertNotIn("<agent>", text)
        for anchor in README_ANCHORS:
            self.assertIn(" ".join(anchor.split()), normalized)
        for fence in self.shell_fences(text):
            self.assertNotRegex(fence, r"<[^>\n]+>")

    def test_public_repository_docs_contain_no_private_material(self):
        forbidden = (
            "/Users/", "BEGIN OPENSSH PRIVATE KEY", "BEGIN RSA PRIVATE KEY",
        )
        paths = [ROOT / "AGENTS.md", ROOT / "CLAUDE.md", ROOT / "README.md"]
        paths.extend((ROOT / ".github").rglob("*.md"))
        for path in paths:
            with self.subTest(path=path.relative_to(ROOT)):
                text = path.read_text(encoding="utf-8").lower()
                self.assertFalse(any(value.lower() in text for value in forbidden))

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
