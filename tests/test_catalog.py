"""Catalog structure and package contracts, verified entirely offline."""

import hashlib
import ast
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXPECTED_SKILLS = {"google-analytics", "google-auth", "google-merchant",
                   "google-pagespeed-insights", "google-search-console"}

#: Which packages still declare owner-supplied values, and which no longer can. A package that
#: signs in through Rundesk's broker declares nothing: its client and its grant are Rundesk's, and
#: a `rundesk.json` naming them would be the second place a credential lived.
DECLARING = {"google-pagespeed-insights"}
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
## Manual user path
## Agent""".splitlines())
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
        self.assertIn("🤖 by <Agent>", pull_request.read_text(encoding="utf-8"))
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
                declared = package / "rundesk.json"
                if entry["name"] not in DECLARING:
                    # A brokered package declares nothing at all. Left behind, its old client and
                    # refresh-token names would still be prompted for by `rundesk skills configure`
                    # and still reported missing by `doctor` — an owner told to place credentials
                    # that nothing reads any more.
                    self.assertFalse(declared.exists(),
                                     f"{entry['name']} signs in through Rundesk and must declare "
                                     "no credentials of its own")
                    continue
                declaration = json.loads(declared.read_text(encoding="utf-8"))
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

    def test_no_package_carries_its_own_oauth_client_or_grant(self):
        """The migration has one direction, and a leftover is worse than a clean break.

        Each of these named one Google identity's whole grant, per package. They are replaced by
        Rundesk holding the grant once; a package that still declared one would be asking an owner
        to configure a second, unread copy of the credential this catalog no longer touches.
        """
        gone = ("_CLIENT_ID", "_CLIENT_SECRET", "_REFRESH_TOKEN")
        for entry in self.manifest["skills"]:
            package = ROOT / entry["path"]
            for one in sorted(package.rglob("*")):
                # Shipped runtime and documentation only. A suite may legitimately *name* one of
                # these to prove it is not read and not leaked, and a rule that forbade the name
                # everywhere would forbid exactly the test that proves the rule.
                if one.name.startswith("test-"):
                    continue
                if one.is_file() and one.suffix in (".py", ".json", ".md"):
                    said = one.read_text(encoding="utf-8", errors="replace")
                    for name in gone:
                        with self.subTest(file=str(one.relative_to(ROOT)), name=name):
                            self.assertNotIn(f"GOOGLE_ANALYTICS{name}", said)
                            self.assertNotIn(f"GOOGLE_MERCHANT{name}", said)
                            self.assertNotIn(f"GOOGLE_SEARCH_CONSOLE{name}", said)

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


class GoogleSignInGuidance(unittest.TestCase):
    """What the setup documentation must say, because getting it wrong is the usual failure."""

    OAUTH_PACKAGES = ("google-analytics", "google-search-console", "google-merchant")

    def reference(self):
        return (ROOT / "skills" / "google-auth" / "references" / "cli.md").read_text(
            encoding="utf-8")

    def test_the_owner_places_the_app_client_before_signing_in(self):
        said = self.reference()
        compact = " ".join(said.split())
        self.assertIn("rundesk env set GOOGLE_OAUTH_CLIENT_ID", said)
        self.assertIn("rundesk env set GOOGLE_OAUTH_CLIENT_SECRET", said)
        # Placed first, then signed in. Read inside the one step that gives the commands, because
        # `login` is legitimately named earlier in prose and anchoring on the whole page would make
        # this assert layout rather than instruction.
        step = said[said.index("5. Place the client values"):]
        step = step[:step.index("```", step.index("```") + 3)]
        self.assertLess(step.index("rundesk env set GOOGLE_OAUTH_CLIENT_ID"),
                        step.index("rundesk login google"))
        self.assertLess(step.index("rundesk env set GOOGLE_OAUTH_CLIENT_SECRET"),
                        step.index("rundesk login google"))
        self.assertIn("asks for nothing", compact)

    def test_no_google_package_ever_asks_anyone_for_a_credential(self):
        for name in ("google-auth", *self.OAUTH_PACKAGES):
            said = " ".join((ROOT / "skills" / name / "SKILL.md").read_text(
                encoding="utf-8").split())
            with self.subTest(package=name):
                self.assertIn("Never ask anyone for a client ID", said)
                self.assertIn("refresh token", said)

    def test_the_desktop_client_and_its_loopback_callback_are_documented(self):
        compact = " ".join(self.reference().split())
        for said in ("Desktop app", "127.0.0.1", "<ephemeral-port>", "<random-path>",
                     "No fixed port", "not `localhost`",
                     "No manual copy-and-paste", "return to the terminal"):
            with self.subTest(said=said):
                self.assertIn(said, compact)

    def test_api_enablement_consent_scopes_and_resource_access_are_told_apart(self):
        compact = " ".join(self.reference().split())
        for said in ("API enablement", "OAuth consent scopes", "Resource permission",
                     "Account selection", "Google Analytics Data API",
                     "Google Analytics Admin API", "Search Console API", "Merchant API",
                     "must already have access to the Analytics properties"):
            with self.subTest(said=said):
                self.assertIn(said, compact)

    def test_the_webmasters_scope_says_why_it_is_not_the_readonly_one(self):
        compact = " ".join(self.reference().split())
        self.assertIn("auth/webmasters", compact)
        self.assertIn("sitemap submission mutates", compact)
        self.assertIn("analytics.readonly", compact)
        self.assertIn("auth/content", compact)

    def test_no_skill_leads_a_reader_to_type_a_profile(self):
        """The default app is the whole mental model; `--profile` is a marked escape hatch.

        Checked on the command examples rather than on the prose, because an example is what gets
        copied — a guide can say "rarely needed" and still teach the opposite in its code block.
        """
        for name in ("google-auth", *self.OAUTH_PACKAGES):
            said = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            with self.subTest(package=name):
                examples = [one for one in said.splitlines()
                            if one.lstrip().startswith(('"$RUNDESK_SKILLS', "  --", "rundesk "))]
                self.assertTrue(examples)
                for one in examples:
                    self.assertNotIn("--profile", one)
                self.assertIn("--email", said)
                self.assertIn("almost never right", " ".join(said.split()))


class NoShadowedDefinition(unittest.TestCase):
    """A name defined twice in one scope is the second one winning, silently.

    Python does not complain, the suite still reports the same count, and the first definition is
    simply never run — so a case somebody wrote and believes is covering something is dead code
    that goes on passing. This was a real edit here: two rounds of the same insertion left one
    class holding two `gone`, two `stop_orphan`, and two tests of one name, and nothing went red.
    """

    def python_files(self):
        for one in sorted(ROOT.rglob("*.py")):
            if ".git" not in one.parts:
                yield one

    def test_no_module_or_class_defines_one_name_twice(self):
        shadowed = []
        for one in self.python_files():
            tree = ast.parse(one.read_text(encoding="utf-8"), filename=str(one))
            for scope in [tree] + [node for node in ast.walk(tree)
                                   if isinstance(node, ast.ClassDef)]:
                seen = {}
                for node in scope.body:
                    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                             ast.ClassDef)):
                        continue
                    if node.name in seen:
                        where = getattr(scope, "name", one.stem)
                        shadowed.append(f"{one.relative_to(ROOT)}: {where}.{node.name} "
                                        f"at lines {seen[node.name]} and {node.lineno}")
                    seen[node.name] = node.lineno
        self.assertEqual([], shadowed, "a later definition silently replaces an earlier one")


if __name__ == "__main__":
    unittest.main()
