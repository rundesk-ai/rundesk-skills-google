# Rundesk Google Skills

Guarded Google service integrations packaged as reusable Agent Skills with self-contained command
runtimes, OAuth profiles, and offline tests. The catalog is read-only apart from Search Console's
confirmation-guarded sitemap submission.

## Skills

- `google-analytics` - accessible GA4 accounts and properties, bounded traffic, audience, key-event,
  ecommerce, and direct Analytics Data API reports.
- `google-merchant` - Merchant Center accounts, product serving status, item issues, bounded product
  performance, and price and market insights.
- `google-pagespeed-insights` - bounded Lighthouse scores, lab metrics, prioritized audit
  findings, and opt-in Chrome UX Report field data for public webpages.
- `google-search-console` - accessible sites, bounded search performance, sitemaps, URL inspection,
  and confirmation-guarded sitemap submission.

## Install

Rundesk CLI installs the complete catalog and keeps OAuth credentials, grants, profiles, caches, and
state outside the package tree. Installation grants no skill automatically.

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google --confirm
rundesk skills grant agent-name rundesk-skills-google/google-search-console
```

The first install command previews the exact change; `--confirm` applies it. Skills use the verified
`<catalog>/<skill>` grant syntax. Updates and removal follow the same preview-first contract:

```sh
rundesk skills update rundesk-skills-google
rundesk skills update rundesk-skills-google --confirm
rundesk skills remove rundesk-skills-google
rundesk skills remove rundesk-skills-google --confirm
```

Configure and inspect a package's OAuth profiles without exposing secret values:

```sh
rundesk skills configure rundesk-skills-google/google-search-console
rundesk skills profiles rundesk-skills-google/google-search-console
rundesk skills doctor agent-name
```

Each package's `references/cli.md` documents its exact Google Cloud setup, credential fields, OAuth
scopes, and resource discovery sequence.

## Requirements

- Python 3.9+ and the standard library. No package manager, virtual environment, shared Google
  runtime, or sibling-package dependency is required.
- The Google API and OAuth configuration declared by the chosen package's `rundesk.json`. PageSpeed
  uses its documented API credential contract; other packages use their documented OAuth clients and
  grants. Never put secret values or grants in the catalog.
- Explicit profile and resource selection when more than one Google identity, account, property,
  site, or resource is available.

Read [ENVIRONMENTS.md](ENVIRONMENTS.md) for OAuth profile resolution, configuration ownership,
credential-file permissions, cache, and state. Read the [Google integration lexicon](docs/lexicon.md)
for canonical product and resource terminology.

## Repository layout

```text
.
├── .github/
│   ├── ISSUE_TEMPLATE/{bug-report.md,change-proposal.md}
│   └── pull_request_template.md
├── docs/lexicon.md
├── skills/
│   └── <name>/
│       ├── SKILL.md
│       ├── rundesk.json
│       ├── references/cli.md
│       └── scripts/
│           ├── <name>
│           └── <name>.d/        implementation and offline tests
├── tests/test_catalog.py
├── AGENTS.md
├── CLAUDE.md
├── ENVIRONMENTS.md
├── RELEASING.md
└── manifest.json
```

Each package is an independent runtime, OAuth grant, profile, resource, and removal boundary.
Runtime files never depend on a sibling package or a root-local library.

## Development

```sh
python3 -m unittest discover -s tests -v
python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q
skills/google-search-console/scripts/google-search-console --help
repository_root="$(pwd)"
(cd /tmp && "$repository_root/skills/google-search-console/scripts/google-search-console" --help)
git diff --check
```

The root suite is the catalog gate and runs every package's offline suite. Tests replace OAuth and
Google API boundaries with synthetic fixtures and never contact the network. Read
[AGENTS.md](AGENTS.md) before contributing for approval, scope, ambiguity, privacy, validation, and
documentation requirements.

## Creating a skill catalog

Use the organization-wide [skill catalog guide](https://github.com/rundesk-ai/rundesk-cli/blob/main/docs/catalogs.md)
for package structure, manifests, runtime isolation, credential declarations, public documentation,
testing, and release contracts. Extend an existing package when it already owns the Google API or
command surface.

## Contributing

- Report reproducible incorrect behavior with the [bug report template](.github/ISSUE_TEMPLATE/bug-report.md).
- Propose a skill, integration, command, or repository improvement with the [change proposal template](.github/ISSUE_TEMPLATE/change-proposal.md).
- Prepare changes with the [pull request template](.github/pull_request_template.md) and provide
  evidence for the exact head commit.

Contributions must keep `README.md`, `manifest.json`, `skills/`, and catalog tests aligned and must
contain no credentials, OAuth grants, personal data, private identifiers, or owner-specific paths.

## Releases

Follow [RELEASING.md](RELEASING.md) for semantic versioning, tags, and publication. Changes to
published catalog contents or runtime behavior require the version treatment it defines.
Process-only guide or template changes, including `AGENTS.md`, `CLAUDE.md`, and GitHub templates, do
not require a manifest version bump.

## License

This repository is licensed under the [MIT License](LICENSE).
