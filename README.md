# Rundesk Google Skills

Guarded Google service integrations packaged as reusable Agent Skills with self-contained command
runtimes and offline tests. Rundesk discovers packages under `skills/`; `manifest.json` is the
catalog index.

The catalog is read-only apart from Search Console's confirmation-guarded sitemap submission. It
separates Search Console from Google Analytics so each skill can request only the OAuth access and
Google resources it needs.

## Install with Rundesk CLI

```sh
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google
rundesk skills install https://github.com/rundesk-ai/rundesk-skills-google --confirm
rundesk skills grant <agent> rundesk-skills-google/google-search-console
rundesk skills grant <agent> rundesk-skills-google/google-analytics
rundesk skills grant <agent> rundesk-skills-google/google-pagespeed-insights
rundesk skills grant <agent> rundesk-skills-google/google-merchant
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

## Signing in to Google

Rundesk owns Google sign-in for every OAuth package here. It holds the OAuth app configuration, runs
the browser flow, keeps the grant sealed, and refreshes tokens; a package declares no credentials and
receives one short-lived access token over a private socket when it runs.

```sh
rundesk login google
rundesk login google --profile acme
```

A profile is one OAuth app configuration, not a person. A profile can hold several verified Google
accounts, so every OAuth command takes `--profile <app-profile>` to choose the app, `--email
<address>` to choose the account, and `--auth` to run `rundesk login google` first. Each is needed
only when more than one answer exists, and ambiguity is refused rather than guessed.

`google-pagespeed-insights` reads public pages with a Google Cloud API key instead, which it
declares in its own `rundesk.json`.

See [ENVIRONMENTS.md](ENVIRONMENTS.md) for the complete ownership contract. Each package's
`references/cli.md` documents its own scope and commands.

## Package isolation

Every command is self-contained and uses only Python's standard library. Runtime files stay inside
their owning package. Search Console and Analytics may follow the same catalog conventions, but
they do not share Python modules, token files, caches, or an undocumented runtime.

Merchant Center is deliberately separate from Analytics: Analytics measures a site's own
sessions and revenue, while Merchant Center holds the product feed, its approval state, and how
those products perform on Google's surfaces.

Recurring names and product boundaries are defined in the high-level
[Google integration lexicon](docs/lexicon.md). API-specific fields and one-off command options stay
in their owning package instead of turning the lexicon into a field inventory.

## Included skills

- `google-search-console` — accessible sites, bounded and filterable search performance reports,
  sitemaps, URL inspection, and confirmation-guarded sitemap submission.
- `google-analytics` — accessible GA4 accounts and properties, bounded traffic, audience, key-event,
  and ecommerce reports, plus direct Analytics Data API queries.
- `google-pagespeed-insights` — bounded Lighthouse scores, lab metrics, and prioritized audit
  findings for public webpages.
- `google-merchant` — Merchant Center accounts, product serving status, item-issue diagnostics,
  bounded product performance reports, and Google's price and market insights.

Maintainers use [RELEASING.md](RELEASING.md).
