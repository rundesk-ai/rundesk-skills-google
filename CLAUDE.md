# AGENTS

Rules for every agent working in this repository. These instructions define how to work here;
where they conflict with general habits, this file wins.

## Purpose

This repository publishes Rundesk's guarded Google service integration skills. Each package ships
its own CLI, operating guidance, OAuth credential declaration, and offline tests.

- `README.md` defines the public catalog and install surface.
- `ENVIRONMENTS.md` defines package runtime, OAuth profiles, configuration, and state boundaries.
- `docs/lexicon.md` defines canonical Google catalog terminology and boundary mappings.
- `RELEASING.md` defines versioning and releases.
- Each package's `SKILL.md` and `references/cli.md` define its agent and command contracts.
- The [skill catalog guide](https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md)
  defines the organization-wide catalog contract.

Keep these sources of truth aligned with the shipped files and behavior.

## Before you work

1. Read `README.md`, `ENVIRONMENTS.md`, `docs/lexicon.md`, and `RELEASING.md` when the task touches
   their contracts. Read every `SKILL.md`, reference, script, declaration, and test you will change.
2. Inspect the skills supplied by the runtime and load the smallest complete set that applies. Use
   `writing-skills` for `SKILL.md`, applicable runtime or testing guidance for code and tests,
   `naming-grammar-conventions` for recurring or cross-layer terminology, and `managing-github` for
   pull requests or releases.
3. Search before creating. Reuse or extend the package that already owns a Google API or command
   surface instead of introducing a competing path.
4. Inspect `git status` and the relevant diff before editing. Preserve unrelated work, keep shared
   worktrees safe, and never undo another contributor's changes.
5. Inspect the current official Google API, OAuth, resource, and scope contracts plus the package's
   established conventions before naming or changing a resource, command, scope, or environment key.
   Investigate owner concerns with evidence instead of guessing.

## Repository layout

```text
.
├── .github/
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug-report.md
│   │   └── change-proposal.md
│   ├── pull_request_template.md
│   └── workflows/       CI workflows
├── docs/                catalog-wide terminology and supporting documentation
├── skills/              independently installable Google integration packages
├── tests/               catalog-level structure and contract checks
├── AGENTS.md            agent instructions
├── CLAUDE.md            byte-identical copy of AGENTS.md
├── ENVIRONMENTS.md      runtime, OAuth profile, configuration, and state contract
├── README.md            public catalog documentation
├── RELEASING.md         release procedure
└── manifest.json        published catalog inventory and version
```

Do not add root runtime code or shared package libraries. Scratch work belongs outside the
repository and must be removed when the task ends.

## Package and artifact contract

```text
skills/<name>/
├── SKILL.md
├── rundesk.json
├── references/
│   └── cli.md
└── scripts/
    ├── <name>
    └── <name>.d/
        ├── <name>.py
        └── test-<name>.py
```

- Keep the complete runtime under its owning package. Launchers resolve support files relative to
  their own location and work when invoked outside the repository.
- Use Python 3.9+ and the standard library only. Do not require `pip`, a virtual environment,
  repository setup code, a shared Google runtime, or any sibling package at runtime.
- Keep OAuth credentials and grants, configuration, caches, and mutable state in the locations
  defined by `ENVIRONMENTS.md`, never in the catalog tree.
- Declare only genuinely required values in package-local `rundesk.json`. Keep declared names plain,
  uppercase, free of `__`, prefixed for the package, and aligned with supported OAuth profile fields.
- Credential-free `--help` must exit zero. `profiles` lists local configuration without a network call.
- Bound every read by default, make truncation explicit on stderr, and emit JSON only when requested.
- Require explicit profile and Google resource selection when more than one valid choice exists.
- Preview every mutation first. Execute it only after the owner approves the exact profile, resource,
  request, and effect and the command receives the package's exact confirmation input.

## Safety and approval gates

Obtain explicit owner approval before:

- adding, removing, or changing a dependency;
- adding a command or option that creates, edits, deletes, submits, or otherwise mutates a Google
  resource;
- adding or broadening an OAuth scope, consent, or grant requirement;
- deleting a package, command, or any file outside the task's immediate scope;
- editing `AGENTS.md` or `CLAUDE.md`; or
- committing, pushing, tagging, releasing, or otherwise changing external state unless the request
  already authorizes that exact action.

Never:

- commit or print credentials, client secrets, OAuth grants, refresh or access tokens, authorization
  headers, raw dotenv content, customer or account identifiers, private project names, personal data,
  or absolute owner paths;
- let an offline test contact Google, OAuth endpoints, or any network service;
- use destructive Git commands, history rewrites, broad restore operations, or another contributor's
  work to clean a shared worktree;
- report success for a result Google did not verify, or hide a refusal, permission error, partial
  result, or truncation;
- combine credential fields from different profiles, accounts, credential forms, or sources; or
- guess a profile, Google identity, account, property, site, or other resource when selection is
  absent or ambiguous.

Resolve profiles exactly as `ENVIRONMENTS.md` documents. A named profile never falls back to plain
default-profile credentials. A profile uses one supported credential spelling completely; partial or
conflicting forms are refused. Keep packages isolated even when they use the same Google identity so
scopes, rotation, revocation, and removal stay independent.

## Delegation

- Delegate only bounded, self-contained work with non-overlapping file ownership when it materially
  helps. Give each worker the applicable rules, exact scope, prohibited changes, and required proof.
- Keep requirements, architecture decisions, integration, and final verification in the parent
  context. Review every delegated result before using it.
- Delegation never expands authority. A worker may not commit, push, mutate Google resources, broaden
  OAuth scopes, or make another gated change unless the original request authorized it.
- In a shared worktree, coordinate ownership explicitly, preserve concurrent edits, and never revert
  files to resolve overlap.

## Architecture and conventions

- A package is the runtime, OAuth grant, profile, resource, test, and removal boundary. Packages do not
  import, execute, or depend on sibling packages or root-local helpers.
- Start Python modules with `from __future__ import annotations` to preserve modern annotations at the
  Python 3.9 floor. Prefer standard-library types and `unittest`.
- Keep OAuth and Google API calls behind replaceable boundaries and test with synthetic fixtures.
  Validate response shapes, pagination tokens, origins, redirects, and safe error payloads.
- Use the minimum documented OAuth scope for the supported operation. Preserve explicit identity,
  profile, account, property, site, and resource distinctions; do not collapse different Google
  concepts for convenience.
- Verify current official Google endpoint, method, resource-name, query, scope, and response contracts
  before changing them. Preserve fixed Google names at the boundary and map them to the canonical terms
  in `docs/lexicon.md`.
- Keep command text compact and deterministic. Send operational errors, refusals, and truncation
  notices to stderr and return non-zero when requested work did not happen.
- Comments explain non-obvious decisions, invariants, ordering, vendor behavior, and security
  boundaries. Do not narrate mechanics already clear from the code.
- Use lowercase hyphenated package and command names. Keep the directory, manifest name, launcher,
  credential prefix, frontmatter `name`, and documented command spelling aligned.
- Use `naming-grammar-conventions` and `docs/lexicon.md` when a term recurs across the CLI, Python,
  output, credentials, resources, and documentation.

## Documentation duties

Keep documentation true in the same change that changes behavior:

- Add, remove, or rename a skill: update `manifest.json`, the README skill list, and catalog tests.
- Change OAuth profiles, configuration, cache, or state: update `ENVIRONMENTS.md`.
- Change canonical or cross-layer terminology: update `docs/lexicon.md` and affected surfaces.
- Change the release process: update `RELEASING.md`.
- Change setup, credentials, scopes, endpoints, resources, output, confirmation, or validation: update
  the package's `references/cli.md`.
- Change required credentials: update `rundesk.json`, the command resolver, references, and tests.
- Change triggers, safe defaults, boundaries, or non-obvious agent guidance: update `SKILL.md` using
  `writing-skills`.
- Change either root agent guide: make `AGENTS.md` and `CLAUDE.md` byte-identical in the same change.

Do not duplicate detailed CLI reference material in `SKILL.md`. Keep public examples synthetic,
reference secrets only by variable name, and use reserved domains such as `example.test`.

## Build, test, and run

```sh
python3 -m unittest discover -s tests -v
python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q
skills/google-search-console/scripts/google-search-console --help
repository_root="$(pwd)"
(cd /tmp && "$repository_root/skills/google-search-console/scripts/google-search-console" --help)
git diff --check
```

- Run the root catalog suite for every change. It runs each package's offline suite.
- Run each touched package suite directly and report its exact command, test count, skips, and result.
- Exercise credential-free `--help` and offline `profiles` for a touched launcher.
- Invoke a touched launcher from a directory outside the repository to prove package-relative launch.
- Keep CI offline and compatible with Linux, macOS, Python 3.9, and the configured matrix.
- Run focused checks for documentation or catalog-test changes, compare the two guide files byte for
  byte, inspect the final diff, and run `git diff --check`.

## Pull requests and releases

- Use `.github/pull_request_template.md` for every pull request. Preserve its headings and checklists.
- Base every claim on the exact pull request head commit. Record exact commands and observed results,
  and explain any unavailable or inapplicable check.
- Before handoff or merge, inspect the complete diff and commit-visible artifacts for credentials,
  OAuth grants, personal data, owner or customer identifiers, private-project language, and
  owner-specific paths.
- Require the configured CI checks for the exact head. A prior run, local green result, or different
  commit is not pull request evidence.
- Process-only guide or template changes, including `AGENTS.md`, `CLAUDE.md`, and GitHub templates,
  do not require a manifest version bump. Changes to published catalog contents or runtime behavior
  follow `RELEASING.md`.
- Do not merge, tag, or release unless the request explicitly authorizes it.

## Definition of done

1. Complete the full requested scope and preserve every gate in this file.
2. Run the root catalog suite, every touched package suite, applicable launcher checks, guide parity
   check, focused tests, and `git diff --check`; report exact observed results.
3. Keep `README.md`, `manifest.json`, package directories, tests, `ENVIRONMENTS.md`, `docs/lexicon.md`,
   `RELEASING.md`, `AGENTS.md`, and `CLAUDE.md` synchronized wherever the change touches their contracts.
4. Exercise the material command path through its public launcher with safe synthetic or test data.
   Report any live Google or OAuth proof that was not authorized or available.
5. Inspect the final diff for unrelated changes, secrets, OAuth grants, personal or private identifiers,
   owner paths, debug residue, placeholders, and temporary files.
6. Report what changed, exact checks and counts, manual observations, governing skills used, and every
   unrun check or remaining limitation.
