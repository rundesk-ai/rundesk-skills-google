# Google Search Console CLI reference

## Commands

```text
google-search-console profiles
google-search-console sites --profile example --limit 25
google-search-console performance --profile example --site https://www.example.test/ --days 28 --dimension query --limit 25
google-search-console performance --profile example --site sc-domain:example.test --start-date 2026-07-01 --end-date 2026-07-31 --dimension page --dimension device --limit 100
google-search-console inspect-url --profile example --site https://www.example.test/ --url https://www.example.test/page
google-search-console sitemaps --profile example --site https://www.example.test/ --limit 25
google-search-console performance --profile example --site https://www.example.test/ --dimension page --filter country:equals:usa --filter device:equals:MOBILE
google-search-console submit-sitemap --profile example --site https://www.example.test/ --sitemap https://www.example.test/sitemap.xml
google-search-console submit-sitemap --profile example --site https://www.example.test/ --sitemap https://www.example.test/sitemap.xml --confirm
```

Every command except `submit-sitemap` is read-only. Text is the compact default; pass `--json` for
structured output. `sites`, `performance`, and `sitemaps` default to 25 results and accept `--limit` from 1 to
1,000. When a list is cut to the requested limit, the command warns on stderr that output may be
truncated.

`performance` defaults to the last 28 complete days in Google's Pacific reporting zone
(`America/Los_Angeles`), which is how Search Console buckets rows; a late-evening UTC run therefore
still ends on the prior Pacific day. Use either `--days` or both `--start-date` and `--end-date`,
which are passed to Google verbatim. Supported dimensions are `date`, `country`, `device`, `page`,
`query`, and `searchAppearance`; repeat `--dimension` to group by more than one. Optional
`--search-type` values are `web`, `image`, `video`, `news`, `discover`, and `googleNews`.

## Filtering performance

`--filter DIMENSION:OPERATOR:EXPRESSION` is repeatable and adds Google's `dimensionFilterGroups` to
the Search Analytics request body. Without `--filter` that key is absent, so an unfiltered report
sends exactly the request body it sent before.

```json
{"dimensionFilterGroups": [{"groupType": "and", "filters": [
  {"dimension": "country", "operator": "equals", "expression": "usa"},
  {"dimension": "query", "operator": "contains", "expression": "pricing"}
]}]}
```

Every `--filter` joins that one `and` group, so a row is returned only when it matches all of them.
Google ANDs separate groups together and documents only the `and` group type, so a single group
already expresses every combination this command can build. A filter may name a dimension the report
does not group by.

Filter dimensions are `query`, `page`, `country`, `device`, and `searchAppearance`. Operators are
`equals`, `notEquals`, `contains`, `notContains`, `includingRegex`, and `excludingRegex`. The
argument is split on its first two colons only, so a page or query expression may contain colons and
slashes; the expression travels in the JSON body and is never percent-encoded.

Expressions by dimension:

- `country` with `equals` or `notEquals` takes an ISO 3166-1 alpha-3 code and is lowercased for you,
  so `usa`, `USA`, and `Usa` all work; anything that is not three letters is refused.
- `device` with `equals` or `notEquals` is uppercased to `DESKTOP`, `MOBILE`, or `TABLET`; any other
  value is refused.
- `query` and `page` take literal text, or an RE2 pattern for the two regex operators. RE2 matches
  partially and case-insensitively unless the pattern is anchored with `^` or `$`, or prefixed with
  `(?-i)`.
- `searchAppearance` is sent verbatim in the `AMP_BLUE_LINK` form Search Console reports, because
  Google extends that vocabulary without notice.

`contains`, `notContains`, and both regex operators are always sent exactly as typed, including for
`country` and `device`, because they match part of a value rather than all of it. A malformed filter
is refused before any credential or network use.

## Sitemap submission

`submit-sitemap` is the only command that changes Google's state. It sends Google's
`sitemaps.submit` method:

```text
PUT https://www.googleapis.com/webmasters/v3/sites/{siteUrl}/sitemaps/{feedpath}
```

`{siteUrl}` and `{feedpath}` are each a whole URL, so each is percent-encoded into a single path
segment. The request carries no body and Google answers with an empty body, so a silent 2xx is not
treated as proof. The command reads the sitemap back with `sitemaps.get` on the same path and
reports the entry Google recorded; when Google returns no usable entry it fails instead of claiming
success, and a path Google rewrote is reported on stderr.

Without `--confirm` the command resolves only local configuration, prints the method, full request
URL, and required scope, makes no network call at all, and exits 2. `--sitemap` must be an absolute
`http` or `https` URL. A sitemap outside a URL-prefix property is warned about on stderr because
Google rejects it; a `sc-domain:` property covers every host it verifies, so no warning applies
there.

## Credentials and profiles

The command uses OAuth 2.0 refresh credentials and requests access tokens from Google's token
endpoint as needed. Create an OAuth client in a Google Cloud project with the Search Console API
enabled, complete user authorization with the scope that profile needs, and store the resulting
client ID, client secret, and refresh token through Rundesk. Never commit them.

```text
https://www.googleapis.com/auth/webmasters.readonly   profiles, sites, performance, inspect-url, sitemaps
https://www.googleapis.com/auth/webmasters            everything above, plus submit-sitemap
```

A refresh token carries only the scopes it was granted, and Google does not widen an existing grant.
A profile authorized for `webmasters.readonly` therefore fails `submit-sitemap --confirm` with HTTP
403 until the owner reauthorizes for `https://www.googleapis.com/auth/webmasters` and stores the
resulting new refresh token in place of the old one. Keep a profile on the read-only scope unless
that Google account is meant to submit sitemaps.

For the initial manual setup, create a web OAuth client with
`https://developers.google.com/oauthplayground` as an authorized redirect URI. In Google's OAuth
2.0 Playground, enable **Use your own OAuth credentials**, choose server-side and offline access,
authorize the scope above as the intended Google user, and exchange the code for a refresh token.
Enter the three values only through `rundesk skills configure` in the owner's terminal. Do not send
them through chat or save a Playground link containing credentials or tokens.

An external OAuth consent screen left in **Testing** normally issues refresh tokens that expire in
seven days for these non-profile scopes. Publish the app appropriately or expect to reconnect after
the testing token expires. Revocation, inactivity, Workspace policy, and Google's per-user token
limits can also invalidate a refresh token.

Required variables from `rundesk.json`:

```text
GOOGLE_SEARCH_CONSOLE_CLIENT_ID
GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET
GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN
```

Optional variables are `GOOGLE_SEARCH_CONSOLE_LABEL` and
`GOOGLE_SEARCH_CONSOLE_DEFAULT_PROFILE`. A Rundesk-managed named profile appends a normalized
double-underscore suffix:

```dotenv
GOOGLE_SEARCH_CONSOLE_CLIENT_ID__EXAMPLE=
GOOGLE_SEARCH_CONSOLE_CLIENT_SECRET__EXAMPLE=
GOOGLE_SEARCH_CONSOLE_REFRESH_TOKEN__EXAMPLE=
GOOGLE_SEARCH_CONSOLE_LABEL__EXAMPLE=Example Search Console
```

The command also preserves the local dotenv spelling:

```dotenv
GOOGLE_SEARCH_CONSOLE_PROFILES=example
GOOGLE_SEARCH_CONSOLE_DEFAULT_PROFILE=example
GOOGLE_SEARCH_CONSOLE_EXAMPLE_CLIENT_ID=
GOOGLE_SEARCH_CONSOLE_EXAMPLE_CLIENT_SECRET=
GOOGLE_SEARCH_CONSOLE_EXAMPLE_REFRESH_TOKEN=
GOOGLE_SEARCH_CONSOLE_EXAMPLE_LABEL=Example Search Console
```

Resolution order is process environment, `--env-file`, `GOOGLE_SEARCH_CONSOLE_ENV_FILE`,
`RUNDESK_INTEGRATIONS_ENV`, `${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/google-search-console/env`,
then the legacy `${XDG_CONFIG_HOME:-$HOME/.config}/google-search-console/env`. Within one profile,
Rundesk's suffixed key wins, then the local infix key, then the plain key for the default profile
only. A named profile never falls back to another account's plain credentials.

`profiles` does not contact Google and never prints credential values. A service command requires
an explicit profile when more than one is configured.

## Output

- `sites`: property URL, permission level, profile.
- `performance`: requested dimension keys, clicks, impressions, CTR, average position, profile.
- `inspect-url`: inspection URL, verdict, coverage state, indexing state, last crawl, robots state,
  canonical URLs, profile.
- `sitemaps`: sitemap path, type, submission and download dates, pending state, warning and error
  counts, profile.
- `submit-sitemap` without `--confirm`: property, sitemap, method, request URL, required scope,
  `preview` state, profile. It exits 2 and changes nothing.
- `submit-sitemap --confirm`: property plus the sitemap fields Google returned when the entry was
  read back, `submitted` state, profile.

Errors and truncation warnings go to stderr. OAuth secrets, access tokens, authorization headers,
and raw credential files are never printed.

## Validation

```sh
python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q
skills/google-search-console/scripts/google-search-console --help
skills/google-search-console/scripts/google-search-console submit-sitemap --help
skills/google-search-console/scripts/google-search-console profiles
```

Tests are offline and replace the token and API network boundaries with synthetic responses.

## Official references

- [Search Console API authorization](https://developers.google.com/webmaster-tools/v1/how-tos/authorizing)
- [Google OAuth 2.0](https://developers.google.com/identity/protocols/oauth2)
- [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
- [Sites: list](https://developers.google.com/webmaster-tools/v1/sites/list)
- [Search Analytics: query](https://developers.google.com/webmaster-tools/v1/searchanalytics/query)
- [URL Inspection: index.inspect](https://developers.google.com/webmaster-tools/v1/urlInspection.index/inspect)
- [Sitemaps: list](https://developers.google.com/webmaster-tools/v1/sitemaps/list)
- [Sitemaps: get](https://developers.google.com/webmaster-tools/v1/sitemaps/get)
- [Sitemaps: submit](https://developers.google.com/webmaster-tools/v1/sitemaps/submit)
- [Query your Search analytics data](https://developers.google.com/webmaster-tools/v1/how-tos/search_analytics)
- [RE2 syntax](https://github.com/google/re2/wiki/Syntax)
