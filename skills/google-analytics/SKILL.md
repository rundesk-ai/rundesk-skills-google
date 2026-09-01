---
name: google-analytics
description: Use when the user needs to inspect Google Analytics 4 accounts or properties; establish an onsite measurement baseline for SEO; isolate Organic Search traffic and landing-page behavior; analyze acquisition, audiences, key events, lead dispositions, an ordered ecommerce funnel, purchasers, refunds, or revenue; discover property-specific report fields; or run a bounded historical or realtime GA4 query. It supplies read-only GA4 discovery and reporting through an explicitly selected Google account and property. Do not use for Universal Analytics, changing tags or Analytics configuration, Google Ads, Merchant Center feeds, Search Console performance, or causal claims.
---

# Google Analytics

Run `$RUNDESK_SKILLS/google-analytics/scripts/google-analytics`. Rundesk owns Google sign-in and
hands the command one short-lived token, so never ask for or print a credential. Read
`references/cli.md` for command arguments and output fields, `references/measurement.md` before
interpreting organic, lead, funnel, or commerce value, and `references/sources.md` when verifying a
Google contract or citation.

Start with `profiles`, which shows the accounts Rundesk holds and needs no network, then discover
what the selected account can reach:

```sh
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" profiles
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" accounts --email <address> --limit 25
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" properties --limit 50
```

`--email` names one signed-in Google account, and is needed only when Rundesk holds more than one;
the refusal lists the connected addresses. When nothing is connected, the command says so and names
the command to run. Ask the owner to run `rundesk login google` in their own terminal, or pass
`--auth` to run that sign-in from here when a browser is available. The `google-auth` skill in this
catalog owns sign-in, the list of connected accounts, and the Google Cloud setup.

`--profile <app-profile>` exists and is almost never right: it selects a second OAuth **app**, for
an installation with two Google Cloud projects. Do not add it to a command you construct, and never
use it to choose an account — that is always `--email`. Never ask anyone for a client ID, a client
secret, or a refresh token.

Never guess an account or a property. Use the exact numeric property ID returned by
`properties`.

## Answer the common questions with the bounded reports

Prefer these over hand-built field lists; each one already carries the current GA4 field names for
its question and returns the largest rows first.

```sh
google-analytics traffic --property <id> --breakdown channel --limit 25
google-analytics audience --property <id> --breakdown country --limit 25
google-analytics key-events --property <id> --breakdown event --limit 25
google-analytics lead-lifecycle --property <id> --breakdown event --limit 25
google-analytics commerce --property <id> --breakdown item --purchased-only --limit 25
google-analytics funnel --property <id> --funnel ecommerce --limit 25
```

- `traffic` — where sessions came from: `channel`, `source`, `medium`, `source-medium`, `campaign`,
  `landing-page`, or `date`. Add `--scope first-user` for the channel that first acquired the user
  instead of the session channel; `landing-page` and `date` have no first-user form.
- `audience` — named GA4 audiences or aggregated audience traits: `audience`, `country`, `region`, `city`, `language`, `device`, `browser`,
  `operating-system`, `platform`, `age`, `gender`.
- `key-events` — leads and other key events, GA4's current name for conversions. Broken down by
  `event`, `date`, or `channel`, and always restricted to events the property marks as key events.
  Add `--event generate_lead,purchase` to isolate named events.
- `lead-lifecycle` — Google's recommended lead creation, working, qualification,
  disqualification, converted, and unconverted disposition events. It does not require every
  lifecycle state to be marked as a key event.
- `commerce` — shopping behavior: `item`, `item-id`, `brand`, `category`, `list` report item views,
  cart, checkout, purchase, rate, gross, refund, and net item evidence; `date` and `channel` report
  purchases, purchasers, rates, gross revenue, refunds, and net revenue. `--purchased-only` drops
  rows with no purchase in the window.
- `funnel` — the closed, ordered ecommerce sequence `view_item` → `add_to_cart` →
  `begin_checkout` → `purchase`. It reports users, completion rate, abandonments, and abandonment
  rate at each step. Google's recommended lead events do not define a canonical stage order, so do
  not infer a lead funnel from their names.

Add `--segment organic-search` to any bounded report above to isolate GA4's full Organic Search
default channel group. Use `--segment google-organic` only for the narrower `google / organic`
subset when reconciling Google Search activity. These are session-acquisition filters unless
`traffic --scope first-user` explicitly selects first-user acquisition.

Every one of these accepts `--start-date`, `--end-date`, `--limit`, `--email`, and `--json`, and
defaults to `28daysAgo` through `today`.

Use `report` and `realtime` only when the question needs a field combination the six bounded
reports do not cover:

```sh
google-analytics report --property <id> --metrics sessions,activeUsers --dimensions date --limit 100
google-analytics realtime --property <id> --metrics activeUsers --dimensions country --limit 25
google-analytics metadata --property <id> --kind metrics --query lead --limit 100
google-analytics compatibility --property <id> --dimensions landingPage --metrics sessions,keyEvents --limit 25
```

`compatibility` uses the supplied Core report fields as context and lists fields that could be
added without breaking that context. It is not an exact pass/fail check of only the supplied set;
Google refuses the request when that starting set is already incompatible.

## Read the results honestly

These commands report only what the property already collects. Missing ecommerce, lead lifecycle,
or key-event rows can be an instrumentation, consent, scope, or processing gap rather than zero
demand. Flag missing required measurement before recommending growth or content expansion.

Google's own caveats arrive on stderr: incomplete current-day data, changing modeled key events,
withheld low-volume rows, an `(other)` rollup, sampling, and reporting currency. Repeat them when
they change what the numbers mean. Age and gender are thresholded and need Google signals enabled.

Search Console is the source of truth for Google Search impressions and clicks; Analytics measures
onsite behavior. Reconcile GA revenue and lead events to the payment, order, or CRM system of
record. Never describe a channel association or attribution model as proof of causal impact.

Analytics measures the property only. Product feeds and item availability live in Merchant Center,
and ad spend lives in Google Ads; neither is reachable from here.

Keep dimensions, metrics, date ranges, and row limits no broader than the question requires. Human
output is compact CSV. Use `--json` only for downstream processing or when normalized JSON fields
are explicitly needed.

All commands are read-only. This package cannot create or edit Analytics accounts, properties,
streams, events, audiences, access, or configuration.
