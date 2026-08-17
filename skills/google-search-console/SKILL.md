---
name: google-search-console
description: Use when the user needs Google Search Console property discovery, organic search performance, URL index inspection, or sitemap status. It supplies bounded read-only evidence through Google's Search Console APIs. Do not use for general web search, Google Analytics, SEO recommendations without Search Console evidence, or sitemap submission and other Search Console changes.
---

# Google Search Console

Run `$RUNDESK_SKILLS/google-search-console/scripts/google-search-console`; it resolves credentials
itself, so never inspect or print their source. Read `references/cli.md` only for setup, environment
keys, complete output fields, API behavior, or validation.

Start with profiles and properties. Never guess a profile or property when more than one is
available:

```sh
"$RUNDESK_SKILLS/google-search-console/scripts/google-search-console" profiles
"$RUNDESK_SKILLS/google-search-console/scripts/google-search-console" sites --profile <profile> --limit 25
```

Keep performance reads narrow. Default to the last 28 complete days and a small row limit; add only
the dimensions needed for the question:

```sh
"$RUNDESK_SKILLS/google-search-console/scripts/google-search-console" performance \
  --profile <profile> --site <property> --dimension query --limit 25
"$RUNDESK_SKILLS/google-search-console/scripts/google-search-console" inspect-url \
  --profile <profile> --site <property> --url https://www.example.test/page
"$RUNDESK_SKILLS/google-search-console/scripts/google-search-console" sitemaps \
  --profile <profile> --site <property> --limit 25
```

A URL-prefix property includes its trailing slash; a domain property starts with `sc-domain:`.
Always reuse the exact property identifier returned by `sites`.

Performance rows are aggregated and may omit anonymized queries. Treat clicks, impressions,
click-through rate, and average position as Search Console measurements, not complete traffic or
ranking truth. State the date range and dimensions with findings.

This package is read-only. It cannot add properties, submit or delete sitemaps, request indexing,
or change Search Console configuration.
