# Google Analytics CLI reference

## Commands

```text
google-analytics profiles
google-analytics accounts --profile example --limit 25
google-analytics properties --profile example [--account 123456] --limit 50
google-analytics report --profile example --property 987654321 \
  --start-date 28daysAgo --end-date today \
  --metrics sessions,activeUsers --dimensions date --limit 100
google-analytics realtime --profile example --property 987654321 \
  --metrics activeUsers --dimensions country --limit 25
```

`accounts` and `properties` use the Analytics Admin API's account summaries. `report` and
`realtime` use the GA4 Data API. Every read is bounded by `--limit`; the command reports truncation
to stderr when Google indicates more results exist.

Report metrics and dimensions are comma-separated Google API names. `report` accepts Google date
forms such as `today`, `yesterday`, and `28daysAgo`, or ISO dates.

## Credentials and profiles

The integration calls Google directly through HTTPS and requests only
`https://www.googleapis.com/auth/analytics.readonly`. The OAuth client must belong to a Google Cloud
project where the Google Analytics Data API and Google Analytics Admin API are enabled. The Google
identity that granted the refresh token must have access to the requested Analytics resources.

For the initial manual setup, create a web OAuth client with
`https://developers.google.com/oauthplayground` as an authorized redirect URI. In Google's OAuth
2.0 Playground, enable **Use your own OAuth credentials**, choose server-side and offline access,
authorize `https://www.googleapis.com/auth/analytics.readonly` as the intended Google user, and
exchange the code for a refresh token. Enter the three values only through
`rundesk skills configure` in the owner's terminal. Do not send them through chat or save a
Playground link containing credentials or tokens.

An external OAuth consent screen left in **Testing** normally issues refresh tokens that expire in
seven days for this scope. Publish the app appropriately or expect to reconnect after the testing
token expires. Revocation, inactivity, Workspace policy, and Google's per-user token limits can
also invalidate a refresh token.

Required, per `rundesk.json`:

```text
GOOGLE_ANALYTICS_CLIENT_ID
GOOGLE_ANALYTICS_CLIENT_SECRET
GOOGLE_ANALYTICS_REFRESH_TOKEN
```

Rundesk-managed profiles append `__<PROFILE>`:

```dotenv
GOOGLE_ANALYTICS_CLIENT_ID=
GOOGLE_ANALYTICS_CLIENT_SECRET=
GOOGLE_ANALYTICS_REFRESH_TOKEN=

GOOGLE_ANALYTICS_CLIENT_ID__EXAMPLE=
GOOGLE_ANALYTICS_CLIENT_SECRET__EXAMPLE=
GOOGLE_ANALYTICS_REFRESH_TOKEN__EXAMPLE=
GOOGLE_ANALYTICS_LABEL__EXAMPLE=Example Analytics
```

The older dotenv spelling remains supported:

```dotenv
GOOGLE_ANALYTICS_PROFILES=example
GOOGLE_ANALYTICS_DEFAULT_PROFILE=example
GOOGLE_ANALYTICS_EXAMPLE_CLIENT_ID=
GOOGLE_ANALYTICS_EXAMPLE_CLIENT_SECRET=
GOOGLE_ANALYTICS_EXAMPLE_REFRESH_TOKEN=
GOOGLE_ANALYTICS_EXAMPLE_LABEL=Example Analytics
```

Configuration is resolved in this order: process environment, `--env-file`,
`GOOGLE_ANALYTICS_ENV_FILE`, `RUNDESK_INTEGRATIONS_ENV`, the isolated Rundesk integrations path,
then the legacy integration path. Existing process variables always win. Named profiles never fall
back to unsuffixed credentials.

Restrict dotenv files to their owner with `chmod 600`. The CLI warns about broader permissions and
never prints tokens, client secrets, authorization headers, or dotenv contents. Access tokens are
kept in memory for the current invocation only.

## Output

Human-readable discovery commands emit CSV:

```text
account_id,display_name,property_count,profile
123456,Example account,2,example
```

Historical and realtime report output starts with the requested dimensions, followed by metrics
and `profile,property_id`. `--json` emits normalized objects rather than Google's raw response.
Empty result sets still print a CSV header.

## Validation

```sh
python3 "$RUNDESK_SKILLS/google-analytics/scripts/google-analytics.d/test-google-analytics.py" -q
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" --help
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" profiles
```

The test suite is offline and uses synthetic responses. Optional live smoke tests should stop after
bounded `accounts`, `properties`, and one small report. This package has no mutation command.

## Official references

- [Google Analytics Admin API account summaries](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list)
- [Google Analytics Data API runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport)
- [Google Analytics Data API runRealtimeReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runRealtimeReport)
- [Google OAuth 2.0 for web-server applications](https://developers.google.com/identity/protocols/oauth2/web-server)
- [Google OAuth 2.0](https://developers.google.com/identity/protocols/oauth2)
- [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
