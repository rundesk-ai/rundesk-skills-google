# Rundesk Google Skills

Guarded Google service integrations packaged as reusable Agent Skills with self-contained command
runtimes and offline tests. Rundesk discovers packages under `skills/`; `manifest.json` is the
catalog index.

The initial catalog is read-only. It separates Search Console from Google Analytics so each skill
can request only the OAuth access and Google resources it needs.

## Install with Rundesk CLI

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google --confirm
rundesk skills grant <agent> rundesk-skills-google/google-search-console
rundesk skills grant <agent> rundesk-skills-google/google-analytics
```

Installation previews until `--confirm`, installs every package, and grants none automatically.
Skills are addressed as `<catalog>/<skill>`.

```sh
rundesk skills catalogs
rundesk skills update rundesk-skills-google
rundesk skills update rundesk-skills-google --confirm
rundesk skills remove rundesk-skills-google
rundesk skills remove rundesk-skills-google --confirm
```

## Credentials and OAuth profiles

Each package declares its required OAuth configuration in `rundesk.json`. Rundesk stores configured
values outside the catalog and passes them to commands as environment variables. The plain variable
name represents the default profile; `<FIELD>__<PROFILE>` represents a named profile. A named
profile never falls back to a default-profile value.

Profiles keep Google identities and clients explicit, such as separate company and client access.
Commands refuse ambiguous profile and Google resource selection. Tokens, OAuth grants, caches, and
mutable state are never stored below `skills/`.

See [ENVIRONMENTS.md](ENVIRONMENTS.md) for the complete ownership and resolution contract. Each
package's `references/cli.md` documents its exact fields and setup.

## Package isolation

Every command is self-contained and uses only Python's standard library. Runtime files stay inside
their owning package. Search Console and Analytics may follow the same catalog conventions, but
they do not share Python modules, token files, caches, or an undocumented runtime.

Recurring names and product boundaries are defined in the high-level
[Google integration lexicon](docs/lexicon.md). API-specific fields and one-off command options stay
in their owning package instead of turning the lexicon into a field inventory.

## Included skills

- `google-search-console` — accessible sites, bounded search performance reports, sitemaps, and URL
  inspection.
- `google-analytics` — accessible GA4 accounts and properties plus bounded Analytics Data API
  reports.

Maintainers use [RELEASING.md](RELEASING.md).
