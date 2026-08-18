# Google PageSpeed Insights CLI reference

## Commands

```text
google-pagespeed-insights profiles
google-pagespeed-insights analyze --profile example --url https://www.example.test/
google-pagespeed-insights analyze --profile example --url https://www.example.test/ --strategy desktop --category performance --category accessibility --category best-practices --category seo --audit-limit 10
```

The service command is read-only. Text is compact CSV by default; pass `--json` for structured
output. `analyze` defaults to the mobile strategy, the performance category, and 10 failed or
informative audits. `--audit-limit` accepts 0 through 50; zero emits scores and metrics without
individual audit findings. Repeat `--category` to request multiple Lighthouse categories.

The command reports category scores, selected lab metrics, and the highest-weighted audits whose
score is below 1. Audits without a numeric score are omitted from the compact finding list. Google
may return a final analyzed URL after redirects; both requested and final URLs are reported.

`--strategy` and `--category` stay lowercase at the command line and in output. The request maps
them to the uppercase enums the v5 discovery document defines: `MOBILE` and `DESKTOP`, and
`PERFORMANCE`, `ACCESSIBILITY`, `BEST_PRACTICES`, and `SEO`. The lowercase names are also the keys
Lighthouse uses inside the response, so they are what appears in the `category` column.

The Lighthouse result is validated before use: the result, its `categories` and `audits` objects,
each category object, each `auditRefs` list and element, and each audit object must have the shape
the API documents. Scores, audit-reference weights, and numeric metric values must be finite
numbers. A malformed, null, or wrong-shaped response is reported on stderr and exits 2 rather than
producing a partial reading, and JSON output never contains `NaN` or `Infinity`, which are not
valid JSON.

## API key and profiles

Create an API key in a Google Cloud project with the PageSpeed Insights API enabled, restrict it to
the PageSpeed Insights API where practical, and store it through Rundesk. Never commit or send it
through chat.

Required variable from `rundesk.json`:

```text
GOOGLE_PAGESPEED_INSIGHTS_API_KEY
```

Optional variables are `GOOGLE_PAGESPEED_INSIGHTS_LABEL` and
`GOOGLE_PAGESPEED_INSIGHTS_DEFAULT_PROFILE`. A Rundesk-managed named profile appends a normalized
double-underscore suffix:

```dotenv
GOOGLE_PAGESPEED_INSIGHTS_API_KEY__EXAMPLE=
GOOGLE_PAGESPEED_INSIGHTS_LABEL__EXAMPLE=Example PageSpeed
```

The command also supports local profile discovery through
`GOOGLE_PAGESPEED_INSIGHTS_PROFILES=example`. Resolution order is process environment,
`--env-file`, `GOOGLE_PAGESPEED_INSIGHTS_ENV_FILE`, `RUNDESK_INTEGRATIONS_ENV`,
`${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/google-pagespeed-insights/env`, then the
legacy `${XDG_CONFIG_HOME:-$HOME/.config}/google-pagespeed-insights/env`.

`profiles` does not contact Google and never prints API-key values. A service command requires an
explicit profile when more than one is configured. A named profile never falls back to the default
profile's API key.

## Output

- Summary rows: requested URL, final URL, strategy, category, score, fetch time, Lighthouse version,
  and profile.
- Metric rows: First Contentful Paint, Largest Contentful Paint, Speed Index, Total Blocking Time,
  Cumulative Layout Shift, and Interaction to Next Paint when returned by Lighthouse.
- Audit rows: audit identifier, title, score, display value, weighted impact, and profile.

Errors and truncation warnings go to stderr. API keys and request URLs containing the `key` query
parameter are never printed.

## Validation

```sh
python3 skills/google-pagespeed-insights/scripts/google-pagespeed-insights.d/test-google-pagespeed-insights.py -q
skills/google-pagespeed-insights/scripts/google-pagespeed-insights --help
skills/google-pagespeed-insights/scripts/google-pagespeed-insights profiles
```

Tests are offline and replace the Google API network boundary with synthetic responses, including
hostile fixtures for null, wrong-shaped, and non-finite values.

## Official references

- [PageSpeed Insights API](https://developers.google.com/speed/docs/insights/rest)
- [Get started](https://developers.google.com/speed/docs/insights/v5/get-started)
- [runPagespeed method](https://developers.google.com/speed/docs/insights/rest/v5/pagespeedapi/runpagespeed)
- [PageSpeed Insights v5 discovery document](https://pagespeedonline.googleapis.com/$discovery/rest?version=v5)
