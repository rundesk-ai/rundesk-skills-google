# Google Search Console CLI reference

## Commands

```text
google-search-console profiles
google-search-console sites --profile example --limit 25
google-search-console performance --profile example --site https://www.example.test/ --days 28 --dimension query --limit 25
google-search-console performance --profile example --site sc-domain:example.test --start-date 2026-07-01 --end-date 2026-07-31 --dimension page --dimension device --limit 100
google-search-console inspect-url --profile example --site https://www.example.test/ --url https://www.example.test/page
google-search-console sitemaps --profile example --site https://www.example.test/ --limit 25
```

All service commands are read-only. Text is the compact default; pass `--json` for structured
output. `sites`, `performance`, and `sitemaps` default to 25 results and accept `--limit` from 1 to
1,000. When a list is cut to the requested limit, the command warns on stderr that output may be
truncated.

`performance` defaults to the last 28 complete days in Google's Pacific reporting zone
(`America/Los_Angeles`), which is how Search Console buckets rows; a late-evening UTC run therefore
still ends on the prior Pacific day. Use either `--days` or both `--start-date` and `--end-date`,
which are passed to Google verbatim. Supported dimensions are `date`, `country`, `device`, `page`,
`query`, and `searchAppearance`; repeat `--dimension` to group by more than one. Optional
`--search-type` values are `web`, `image`, `video`, `news`, `discover`, and `googleNews`.

## Credentials and profiles

The command uses OAuth 2.0 refresh credentials and requests access tokens from Google's token
endpoint as needed. Create an OAuth client in a Google Cloud project with the Search Console API
enabled, complete user authorization with the read-only scope below, and store the resulting client
ID, client secret, and refresh token through Rundesk. Never commit them.

```text
https://www.googleapis.com/auth/webmasters.readonly
```

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

Errors and truncation warnings go to stderr. OAuth secrets, access tokens, authorization headers,
and raw credential files are never printed.

## Validation

```sh
python3 skills/google-search-console/scripts/google-search-console.d/test-google-search-console.py -q
skills/google-search-console/scripts/google-search-console --help
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
