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
google-analytics traffic --profile example --property 987654321 \
  --breakdown channel [--scope session|first-user] [--segment organic-search] --limit 25
google-analytics audience --profile example --property 987654321 \
  --breakdown country [--segment organic-search] --limit 25
google-analytics key-events --profile example --property 987654321 \
  --breakdown event [--event generate_lead,purchase] [--segment organic-search] --limit 25
google-analytics lead-lifecycle --profile example --property 987654321 \
  --breakdown event [--segment organic-search] --limit 25
google-analytics commerce --profile example --property 987654321 \
  --breakdown item [--purchased-only] [--segment organic-search] --limit 25
google-analytics funnel --profile example --property 987654321 \
  --funnel ecommerce [--segment organic-search] --limit 25
google-analytics metadata --profile example --property 987654321 \
  --kind all [--query lead] --limit 100
google-analytics compatibility --profile example --property 987654321 \
  --dimensions landingPage --metrics sessions,keyEvents --limit 25
```

`accounts` and `properties` use the Analytics Admin API's account summaries. Every other command
uses the GA4 Data API's `runReport`, except `realtime`, which uses `runRealtimeReport`; `funnel`
uses the alpha `runFunnelReport`; `metadata` uses `getMetadata`; and `compatibility` uses
`checkCompatibility`. All use the existing
`analytics.readonly` scope. Every read is bounded by `--limit`; the command reports truncation when
Google indicates or the bounded result shows that more rows can exist.

`report` and `realtime` take comma-separated Google API names in `--metrics` and `--dimensions` and
send them unchanged. Standard names and only Google's documented property-specific families are
accepted. Examples include `customEvent:lead_score`, the legacy pre-October-2020 form
`customEvent:lead_score[generate_lead]`, `sessionKeyEventRate:generate_lead`, and the numeric custom
channel form `sessionCustomChannelGroup:9432931`. Use `metadata` to copy the exact name one property
supports. An unknown colon family, whitespace, path punctuation, extra colon, malformed bracket
suffix, or non-ASCII custom-channel ID is refused before a request. Historical reports accept any
date string Google accepts.

`traffic`, `audience`, `key-events`, `lead-lifecycle`, `commerce`, and `funnel` choose current GA4
fields from bounded choices and share `--property`, `--start-date`, `--end-date`, `--limit`,
`--profile`, `--segment`, and `--json`. They default to `28daysAgo` through `today`, and accept only `YYYY-MM-DD`, `today`,
`yesterday`, or `NdaysAgo`. Each sorts the largest rows first so a small `--limit` returns the top
of the distribution, except a `date` breakdown, which sorts oldest day first. Prefer `yesterday`
for a settled operating baseline; reports ending today emit a processing warning.

## Bounded report fields

`traffic` reports `sessions`, `activeUsers`, `newUsers`, `engagedSessions`, `engagementRate`,
`averageEngagementTimePerSession` (derived as `userEngagementDuration/sessions`), `keyEvents`, and
`totalRevenue`.

| `--breakdown` | `--scope session` | `--scope first-user` |
|---|---|---|
| `channel` | `sessionDefaultChannelGroup` | `firstUserDefaultChannelGroup` |
| `source` | `sessionSource` | `firstUserSource` |
| `medium` | `sessionMedium` | `firstUserMedium` |
| `source-medium` | `sessionSource`, `sessionMedium` | `firstUserSource`, `firstUserMedium` |
| `campaign` | `sessionCampaignName` | `firstUserCampaignName` |
| `landing-page` | `landingPage` | refused |
| `date` | `date` | refused |

`audience` reports `activeUsers`, `newUsers`, `engagedSessions`, `engagementRate`, `eventCount`,
`keyEvents`, and `totalRevenue`, broken down by named GA4 `audience` (`audienceName`), `country`, `region`, `city`, `language`,
`device` (`deviceCategory`), `browser`, `operating-system` (`operatingSystem`), `platform`,
`age` (`userAgeBracket`), or `gender` (`userGender`).

`key-events` reports `keyEvents`, `eventCount`, `activeUsers`, and `totalRevenue`, broken down by
`event` (`eventName`), `date`, or `channel` (`sessionDefaultChannelGroup`).

`lead-lifecycle` reports `eventCount`, `activeUsers`, and `sessions` for the fixed recommended event
set `generate_lead`, `working_lead`, `qualify_lead`, `disqualify_lead`, `close_convert_lead`, and
`close_unconvert_lead`, broken down by `event`, `date`, or session `channel`. It does not apply the
`isKeyEvent` filter because disposition states need not all be marked as key events.

The audience and key-event metric sets are the ones Google pairs with those dimensions in its own
predefined reports, so a user-scoped breakdown such as age stays inside a published combination.

`commerce` breaks down by `item` (`itemName`), `item-id` (`itemId`), `brand` (`itemBrand`),
`category` (`itemCategory`), or `list` (`itemListName`) and reports `itemsViewed`,
`itemsAddedToCart`, `itemsCheckedOut`, `itemsPurchased`, `cartToViewRate`, `purchaseToViewRate`,
`grossItemRevenue`, `itemRefundAmount`, and `itemRevenue`. It breaks down by `date` or `channel`
(`sessionDefaultChannelGroup`) and reports `ecommercePurchases`, `grossPurchaseRevenue`,
`refundAmount`, `purchaseRevenue`, `totalRevenue`, `totalPurchasers`, and `purchaserRate`. Item- and
purchase-scoped metric families stay separate. Purchase and item revenue are net of refunds;
`totalRevenue` can additionally include subscriptions and ad revenue.

`funnel --funnel ecommerce` is the closed ordered sequence `view_item` → `add_to_cart` →
`begin_checkout` → `purchase`. The funnel table returns Google's headers, normally
`funnelStepName`, `activeUsers`, `funnelStepCompletionRate`, `funnelStepAbandonments`, and
`funnelStepAbandonmentRate`.

`lead-lifecycle` reports Google's recommended lead event names without assigning them a stage
order. Google publishes no canonical sequence for those events. A business-specific lead funnel
requires an explicitly documented local stage order, eligibility rule, and shared population; do
not infer one from the event list or aggregate event counts.

`metadata` returns property-specific dimensions or metrics with API name, UI name, category, type,
custom-definition status, deprecated names, blocked reasons, and description. `--kind` accepts
`all`, `dimensions`, or `metrics`; `--query` performs a case-insensitive name and description search.

`compatibility` treats `--dimensions` and `--metrics` as the starting context for a Core report and
lists other fields that can or cannot be added while preserving that context. Google refuses the
request if the starting context is already incompatible. The response is not an exact pass/fail
test of only the supplied fields. `--compatibility compatible|incompatible` restricts the returned
candidate fields. The command does not assess Realtime or funnel combinations because those APIs
have different rules.

## Filtering

Filtering is limited to what isolates a dataset correctly, because a broad filter language belongs
in a query tool rather than a guarded report.

- `key-events` always sends `dimensionFilter` on `isKeyEvent` with an `EXACT` match on `true`, which
  is the GA4 field that replaced `isConversionEvent` when Google renamed conversions to key events
  in May 2024. Without it, a key-event count would include ordinary events.
- `--event` adds a case-sensitive `inListFilter` on `eventName` inside an `andGroup` with the key
  event filter. It accepts at most 25 names, each in GA4's event-name form: a leading letter, then
  letters, digits, or underscores, up to 40 characters. Google permits non-English letters.
- `--purchased-only` sends a `metricFilter` requiring the breakdown's purchase metric to be greater
  than zero: `itemsPurchased` for an item breakdown, `ecommercePurchases` for `date` and `channel`.
- `--segment all` sends no acquisition filter. `organic-search` exactly matches the scoped default
  channel group `Organic Search`. `google-organic` combines exact source `google` and medium
  `organic`. The fields are session-scoped except on `traffic --scope first-user`, where the filter
  uses first-user acquisition fields. Bounded reports accept no arbitrary filter expression.

## Data that does not exist yet

These commands report what the property already collects. Empty commerce, lead-lifecycle, funnel,
or key-event rows can signal missing events, parameters, consent, identity/session association, or
processing rather than zero demand. Age and gender additionally require Google signals and are
thresholded. Read `measurement.md` for the baseline, implementation checks, source reconciliation,
scope rules, and red flags that must be resolved before SEO growth or content recommendations.

Analytics reports the property's own measurement. Merchant Center product feeds and Google Ads cost
are separate products with separate APIs and are not reachable through this package.

## Signing in

Rundesk owns Google sign-in. It runs the browser flow, keeps the grant sealed, and refreshes
tokens. This package holds none of that and declares no credentials: it asks Rundesk for one
short-lived access token over one end of a socket pair it creates itself, and uses that token as a
request header only. The token never reaches an argument, an environment variable, a file, or any
output.

What `google` means — Google's endpoints, identity fields, base scopes, and the scope behind each
capability — is declared by this catalog's `google-auth` package, which owns sign-in, the account
listing, and the Google Cloud setup. This package reads nothing from it and never runs it.

```sh
rundesk login google
rundesk login google --profile acme
```

A *profile* is one OAuth app configuration, not a person. A single profile can hold several verified
Google accounts; Rundesk keys each by Google's immutable subject identifier and selects it by email.

```text
--profile <app-profile>   which OAuth app configuration to use; needed only when more than one exists
--email <address>         which signed-in account to use; needed only when that profile holds several
--auth                    run `rundesk login google` first, forwarding --profile, then continue
```

`profiles` lists the accounts Rundesk holds for one app profile and contacts Google for none of it.
Missing sign-in, an unconfigured app profile, an ambiguous account, and a missing scope each name
the exact command to run.

Rundesk attaches this package's fixed scope to the token and widens consent in the browser itself
when a grant is short:

```text
https://www.googleapis.com/auth/analytics.readonly   every command
```

The OAuth app Rundesk signs in with must belong to a Google Cloud project where the Google Analytics
Data API and Google Analytics Admin API are enabled, and the account that signs in must be able to
reach the requested Analytics resources.

A Rundesk older than the provider-neutral sign-in bridge cannot answer at all; the command says so
and says to update Rundesk. There is no other way to authorize this package: there is no client ID, client secret,
refresh token, dotenv, or `--env-file` to configure.

## Output

Human-readable discovery commands emit CSV:

```text
account_id,display_name,property_count,profile
123456,Example account,2,example
```

Every report command's output starts with the requested dimensions, followed by metrics and
`profile,property_id`. `--json` emits normalized objects rather than Google's raw response. Empty
result sets still print a CSV header.

```text
sessionDefaultChannelGroup,sessions,activeUsers,newUsers,engagedSessions,engagementRate,averageEngagementTimePerSession,keyEvents,totalRevenue,profile,property_id
Organic Search,1204,908,517,842,0.699,54.2,63,4210.75,example,987654321
```

The bounded reports repeat caveats on stderr so a short or recent result is not mistaken for final
truth: current-day processing, key-event modeling changes, withheld rows below aggregation
thresholds, an `(other)` rollup, sampling, Google's empty-result reason, and reporting currency for
revenue. Funnel reports repeat sampling metadata from their funnel table.

## Validation

```sh
python3 "$RUNDESK_SKILLS/google-analytics/scripts/google-analytics.d/test-google-analytics.py" -q
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" --help
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" profiles
```

The test suite is offline: a stand-in Rundesk answers the sign-in bridge exactly as the real one
documents it, and synthetic responses stand in for Google. Optional live smoke tests should stop
after bounded `accounts`, `properties`, and one small report. This package has no mutation
command.

## Official references

`sources.md` maps every request, field, measurement, and interpretation contract to current official
Google documentation. It replaces the retired event-naming URL previously listed here.
