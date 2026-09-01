# GA4 measurement for organic search

Use this reference to establish whether an Analytics property can answer an SEO value question
before interpreting its numbers. The command reads what GA4 received; it cannot repair tags,
consent, event configuration, CRM identity, or revenue records.

## Work in evidence order

1. **Establish the baseline.** Confirm the property, reporting window, timezone, reporting currency,
   Google Search impressions and clicks from Search Console, then Organic Search sessions and users,
   landing pages, key events, lead dispositions, purchases, revenue, and the ordered ecommerce
   funnel from GA4 when commerce applies. Record the metric, source, scope, date window, filter, and known data-quality
   limitations. If a required event or business outcome is absent, treat measurement repair as the
   first action rather than substituting a proxy.
2. **Resolve quality and technical red flags.** Verify event delivery and parameters, duplicate or
   missing transactions, consent effects, referral exclusions and cross-domain behavior, channel
   classification, thresholding, sampling, freshness, and reconciliation to the CRM or order
   system. Page experience and crawl/index findings come from PageSpeed, CrUX, and Search Console;
   GA4 behavior does not diagnose those systems by itself. GA4 does not produce an SEO quality
   score. If a wider SEO workflow uses one, keep its source metrics and red flags visible rather
   than letting the score replace them.
3. **Build growth plans.** Compare complete periods and meaningful landing-page or audience groups,
   keep session acquisition separate from first-user acquisition, and connect observed traffic to
   downstream quality and value. A reporting association is not proof that organic traffic caused
   an outcome.
4. **Expand content only after the first three gates.** Use Search Console demand and page evidence
   together with GA4 engagement and outcome evidence. Do not scale pages while required measurement
   or critical technical findings remain unresolved.

Google documents Search Console as the source of truth for Google Search performance and Analytics
as the source for behavior on the site. It specifically recommends Traffic Acquisition and Landing
Page reports filtered to `google / organic`. Clicks and sessions will not match exactly because of
consent, timezone, attribution, canonical URLs, non-HTML results, and bot handling. See
[Using Search Console and Google Analytics data for SEO](https://developers.google.com/search/docs/monitor-debug/google-analytics-search-console).

## Minimum baseline commands

```sh
google-analytics traffic --property 987654321 --breakdown landing-page --segment organic-search --end-date yesterday --limit 100
google-analytics key-events --property 987654321 --breakdown event --segment organic-search --end-date yesterday --limit 50
google-analytics lead-lifecycle --property 987654321 --breakdown event --segment organic-search --end-date yesterday --limit 25
google-analytics commerce --property 987654321 --breakdown channel --segment organic-search --end-date yesterday --limit 25
google-analytics funnel --property 987654321 --funnel ecommerce --segment organic-search --end-date yesterday --limit 25
google-analytics metadata --property 987654321 --kind metrics --query lead --limit 100
```

Use `--segment organic-search` for GA4's full Organic Search default channel group. Use
`--segment google-organic` only for the narrower `sessionSource=google` and
`sessionMedium=organic` subset Google recommends when reconciling to Google Search. Neither option
changes attribution settings or proves incrementality.

## Instrument events before relying on them

Prefer GA4's event hierarchy: automatically collected events, enhanced measurement, recommended
events, then custom events only when no recommended event fits. Recommended names preserve standard
reporting behavior; event names are case-sensitive and can use non-English letters. Google caps
event names at 40 characters and reserves some names and prefixes. See
[Set up events](https://developers.google.com/analytics/devguides/collection/ga4/events),
[Recommended events](https://developers.google.com/analytics/devguides/collection/ga4/reference/recommended-events),
and [event collection limits](https://support.google.com/analytics/answer/9267744).

For lead quality, instrument and reconcile the recommended lifecycle events:
`generate_lead`, `working_lead`, `qualify_lead`, `disqualify_lead`, `close_convert_lead`, and
`close_unconvert_lead`. The `lead-lifecycle` command deliberately does not require these events to
be marked as key events. A lead can emit several lifecycle events, so their aggregate counts are
transitions, not a mutually exclusive snapshot of current lead disposition. Reconcile cohort and
current-state counts to the CRM before calculating disposition shares.

Google's recommended lead event list does not establish a canonical funnel order. Define a lead
funnel only from the business's documented local stage sequence, entry and eligibility rules, and
shared lead population. Do not infer that process from event names or turn aggregate lifecycle
counts into a funnel.

For downstream CRM events sent with Measurement Protocol, preserve the same client or app-instance
identity. Include the online `session_id` and send within 24 hours of session start when session
attribution is required. GA4 is not the lead system of record. See Google's
[Measurement Protocol use cases](https://developers.google.com/analytics/devguides/collection/protocol/ga4/use-cases).

For ecommerce, send the recommended sequence `view_item`, `add_to_cart`, `begin_checkout`, and
`purchase`, including the documented `currency`, `value`, `transaction_id`, and `items` parameters;
each item needs `item_id` or `item_name`, and accurate price and quantity are required for useful
item revenue. Send `refund` when money is returned. A stable, unique `transaction_id` lets GA4
deduplicate repeated purchase events. Validate the implementation against Google's
[ecommerce measurement guide](https://developers.google.com/analytics/devguides/collection/ga4/ecommerce),
[ecommerce validation guide](https://developers.google.com/analytics/devguides/collection/ga4/validate-ecommerce),
and [transaction-ID guidance](https://support.google.com/analytics/answer/12313109).

Verify a new or repaired implementation with Tag Assistant, DebugView, and the browser network
request before trusting a report. A missing row can mean missing instrumentation, consent loss,
wrong parameters, a date/scope mismatch, or processing delay; it does not establish zero demand.
Google's [collection troubleshooting guide](https://developers.google.com/analytics/devguides/collection/ga4/troubleshoot)
documents those checks.

When journeys cross related domains, validate Google's
[cross-domain measurement](https://developers.google.com/tag-platform/devguides/cross-domain)
setup and review [unwanted referrals](https://support.google.com/analytics/answer/10327750),
especially payment processors that can overwrite acquisition. Verify that the site's consent
implementation follows Google's [consent-mode guidance](https://developers.google.com/tag-platform/security/guides/consent)
before interpreting missing users or events as behavior.

## Interpret scope, funnels, and value carefully

- Session-scoped acquisition connects activity to the channel that initiated that session.
  First-user acquisition connects a user and later activity to the channel that first acquired the
  user. Event-scoped attribution assigns credit to key events. These answer different questions;
  Google's [traffic-source scope guide](https://support.google.com/analytics/answer/11080067)
  defines the distinction.
- `key-events --breakdown channel` and every organic segment in this package use session acquisition
  fields. Do not describe the result as event-scoped attribution credit. An attribution model
  distributes reporting credit; it does not establish causal lift.
- `funnel` is an ordered, closed user funnel. Aggregate event counts are not a funnel and should not
  be used to calculate step completion without a shared user population. Funnel reporting remains
  a Data API alpha surface, so preserve the exact command and date window with the result.
- Lead lifecycle event counts are transitions. Do not divide one aggregate event count by another
  and label the result a disposition or stage-conversion rate; use the business's explicitly
  defined lead process and the CRM for authoritative cohorts and current states.
- `purchaseRevenue` and `itemRevenue` are net of refunds in GA4. Keep gross purchase or item
  revenue, refund amount, and net revenue separate. `totalRevenue` can also include subscription and
  ad revenue. Reconcile GA4 revenue, purchasers, and transactions to the payment or order system
  before treating them as booked business results.
- Standard reports can take 24–48 hours to process. Attributed and modeled key-event results can
  continue changing for up to 12 days. Prefer complete periods ending at `yesterday` for a stable
  operating baseline, and label any provisional result. See Google's
  [data freshness guide](https://support.google.com/analytics/answer/11198161) and
  [key-event processing guidance](https://support.google.com/analytics/answer/10596866).

## Red flags to surface before recommendations

- Required impression, click, session, key-event, lead-disposition, purchase, or revenue evidence is
  missing or cannot be reconciled to its system of record.
- Organic landing pages have sessions but no downstream lifecycle or purchase instrumentation.
- `purchase` lacks stable transaction IDs, currency, value, or items; refunds are never sent;
  purchases are duplicated; or gross, refund, and net revenue are conflated.
- Lead status exists in the CRM but the recommended lifecycle events are absent, stale, or cannot be
  tied back to the relevant acquisition session.
- A report mixes first-user, session, and event attribution scopes or presents attributed outcomes
  as causal impact.
- The window includes today without a provisional-data label, compared periods are not complete and
  equivalent, or changing modeled results are presented as final.
- Thresholding, sampling, `(other)` rows, consent behavior, or custom-field blocked reasons make the
  apparent audience or outcome distribution incomplete.
- Search Console clicks and GA4 sessions are forced to match instead of documenting the expected
  measurement differences.

Resolve or explicitly bound these red flags before moving from baseline to technical remediation,
growth planning, or content expansion.
