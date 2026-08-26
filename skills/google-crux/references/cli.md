# google-crux CLI reference

## Commands

```text
google-crux profiles
google-crux current --profile example --url https://www.example.test/page --form-factor phone
google-crux current --profile example --origin https://www.example.test --metric lcp --metric inp
google-crux history --profile example --origin https://www.example.test --periods 25
google-crux history --profile example --url https://www.example.test/page --periods 40 --distributions
```

`current` calls `records:queryRecord`; `history` calls `records:queryHistoryRecord`. Both are
read-only POST requests whose JSON body contains exactly one of `url` or `origin`, the selected Core
Web Vitals, and an optional form factor. `history` also sends `collectionPeriodCount` from 1 to 40.

`--form-factor` accepts `all`, `phone`, `tablet`, or `desktop` and defaults to `all`. The command
omits `formFactor` for `all`; the other values map to Google's uppercase enums. Repeat `--metric`
to select `lcp`, `inp`, or `cls`. With none specified, all three are requested in that order.

## Current and history

CrUX current data is one 28-day rolling collection period, updated daily with an approximate
two-day lag. History is one snapshot per week, and every snapshot is another overlapping 28-day
window. The default 25 periods provide about six months; the maximum 40 provide about ten months.
History updates weekly and should not change between weekly refreshes.

Page and origin are distinct evidence scopes. A page needs enough eligible traffic on its own.
`--origin` aggregates eligible experiences across the exact scheme and host. Google may normalize a
URL for lookup; output preserves the requested identifier and reports the record identifier Google
returned rather than silently relabeling it.

## Output

CSV is the default; `--json` emits the same normalized rows. A percentile row contains:

```text
row_type,metric,unit,p75,eligible,first_date,last_date,scope,requested,identifier,form_factor,profile
```

`--distributions` adds one `distribution` row per histogram bucket with `bucket_start`,
`bucket_end`, and `density`. An empty end is the open-ended final bucket. The package preserves
Google's CLS decimal strings rather than converting them to another scale.

Historical arrays are joined to collection periods by index. Their lengths must match exactly.
Google represents a period with no eligible history as `null` p75 and `"NaN"` densities; output
uses an empty value and `eligible=false`. Mixed, non-finite, out-of-range, overlapping, unordered,
or incomplete distributions are refused rather than turned into a trend.

The response is bounded to three requested metrics, 40 collection periods, and at most 10 histogram
buckets per metric. Error bodies and successful JSON bodies are bounded as well. Redirects are
refused so the API key never crosses an unexpected request boundary.

## API key and profiles

Create an API key in a Google Cloud project with the **Chrome UX Report API** enabled, restrict it
to that API where practical, and store it through Rundesk. Never commit it or send it through chat.

Required variable from `rundesk.json`:

```text
GOOGLE_CRUX_API_KEY
```

Optional variables are `GOOGLE_CRUX_LABEL` and `GOOGLE_CRUX_DEFAULT_PROFILE`. A named Rundesk
profile appends the normalized double-underscore suffix:

```dotenv
GOOGLE_CRUX_API_KEY__EXAMPLE=
GOOGLE_CRUX_LABEL__EXAMPLE=Example CrUX
```

The command also supports `GOOGLE_CRUX_PROFILES=example`. Resolution order is process environment,
`--env-file`, `GOOGLE_CRUX_ENV_FILE`, `RUNDESK_INTEGRATIONS_ENV`,
`${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/google-crux/env`, then
`${XDG_CONFIG_HOME:-$HOME/.config}/google-crux/env`.

`profiles` never contacts Google and never prints API-key values. A service command requires an
explicit profile when more than one is configured. A named profile never falls back to the default
profile's key.

## Validation

```sh
python3 skills/google-crux/scripts/google-crux.d/test-google-crux.py -q
skills/google-crux/scripts/google-crux --help
skills/google-crux/scripts/google-crux profiles
```

Tests are offline and replace the CrUX network boundary with synthetic fixtures. No live Google
request is required for catalog verification.

See [sources](sources.md) for the official endpoint, field, metric, update, eligibility, and quota
contracts used here.
