# AGENTS

Rules for every agent working in this repository. These rules are law where they conflict with
general habits.

This repository publishes Rundesk's guarded Google service integrations. Each service owns its
runtime, operating guidance, credential declaration, and offline tests. `README.md` describes the
catalog, `ENVIRONMENTS.md` defines credentials and state, and `RELEASING.md` defines releases.

## Before you work

1. Read `README.md`, `ENVIRONMENTS.md`, and every `SKILL.md` you will touch.
2. Load the governing skill for the artifact. Use `writing-skills` for `SKILL.md`,
   `python-patterns` for Python and tests, `building-integration-clis` for an integration package,
   and `managing-github` for pull requests or releases. If one is unavailable, report that fact.
3. Confirm an existing package does not already own the Google API before adding another.
4. Inspect the current Google API contract and the package's existing conventions before naming a
   resource, command, scope, or environment variable.

## Approval gates

Explicit owner approval is required for:

- a command that changes a Google resource;
- a runtime dependency outside Python's standard library;
- deleting or renaming a package or command;
- changing this file;
- committing, pushing, tagging, or releasing unless the task authorizes it.

## Catalog invariants

- Keep `manifest.json`, the README skill list, and `tests/test_catalog.py` aligned with `skills/`.
- Keep every runtime file under its owning `skills/<name>/` package. Google packages do not import,
  execute, or depend on files from sibling packages.
- Keep tests offline. Replace OAuth and Google API network boundaries with synthetic fixtures.
- Never commit credentials, OAuth grants, refresh tokens, customer identifiers, private project
  names, or absolute owner paths.
- Never print tokens, client secrets, authorization headers, or raw credential files.
- Credential-free `--help` exits zero. `profiles` lists local configuration without a network call.
- Reads are bounded by default. Truncation is explicit. JSON output is opt-in.
- Refuse ambiguous profile, account, property, or site selection.
- A command reports success only after the requested result is verified.
- Comments explain non-obvious decisions, invariants, ordering, and security boundaries. Do not
  narrate mechanics already expressed by the code or use comments to compensate for vague names.

## Package contract

```text
skills/<name>/
├── SKILL.md
├── rundesk.json
├── references/cli.md
└── scripts/
    ├── <name>
    └── <name>.d/
        ├── <name>.py
        └── test-<name>.py
```

The runtime is Python 3.9+ and standard-library only. A launcher resolves support files relative to
its own location. Credentials, caches, and mutable state remain outside the catalog because an
update replaces a package atomically. See `ENVIRONMENTS.md`.

## Build and test

```sh
python3 -m unittest discover -s tests -v
python3 skills/<name>/scripts/<name>.d/test-<name>.py -q
skills/<name>/scripts/<name> --help
```

Run the launcher once from outside the repository to prove it does not depend on the working
directory.

## Definition of done

1. The root catalog suite and every touched package suite pass offline.
2. `README.md`, `manifest.json`, and the actual package directories agree.
3. No dependency, credential, private identifier, or cross-package runtime was introduced.
4. The final report names any governing skill that could not be loaded.
