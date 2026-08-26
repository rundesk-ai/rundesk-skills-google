# Codemap — rundesk-skills-google

Where each part lives. Counts are of artifacts, so they survive a rename and go wrong visibly when
the tree moves on without this page.

Every package is a directory under `skills/` holding a `SKILL.md`, the references it loads on
demand, and the one command it ships under `scripts/`. Nothing else in the repository is large.

## Packages (skills/ — 6, 9 reference files)

Each holds `SKILL.md` for routing and core procedure, and `references/` for detail loaded on demand.
`references/sources.md` is required in every touched package.

| Package | References | Command |
|---|---|---|
| `google-analytics` | 1 | yes |
| `google-auth` | 1 | yes |
| `google-crux` | 2 | yes |
| `google-merchant` | 1 | yes |
| `google-pagespeed-insights` | 2 | yes |
| `google-search-console` | 2 | yes |

6 of 6 packages ship a command under `scripts/`.

## Identity (root)

| File | What it is |
|---|---|
| `manifest.json` | schema, name, version (`0.4.0`), and description |
| `README.md` | the consumer contract: what the catalog is, how to install it, and every package |
| `ENVIRONMENTS.md` | the OAuth ownership, configuration, cache, and state contract |
| `AGENTS.md`, `CLAUDE.md` | the repository guide, byte-identical by contract |
| `RELEASING.md` | the publication contract |

## Tests (tests/ — 1 suite)

The repository contract: the manifest and the tree agree, every package is complete and correctly
named, the README lists exactly what ships, and the guide pair stays byte-identical.

## Automation (.github/)

Issue templates, the pull-request template, and the workflow that runs the suite.

## Documentation (docs/)

`README.md`, `BRIEF.md`, and `CODEMAP.md` at the root, plus `concepts/`.
