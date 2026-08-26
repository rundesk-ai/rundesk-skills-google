---
name: google-crux
description: Use when the user needs current or historical Chrome UX Report evidence for a public URL or origin, including Core Web Vitals percentiles, distributions, form-factor comparisons, or a six-month performance trend. It supplies bounded read-only LCP, INP, and CLS field evidence through the CrUX and CrUX History APIs. Do not use for Lighthouse lab audits, private pages, synthetic performance testing, Search Console, or changing a website.
---

# Google Chrome UX Report

Run `$RUNDESK_SKILLS/google-crux/scripts/google-crux`; it resolves the API key itself, so never
inspect or print its source. Read `references/cli.md` for setup, profiles, output fields, response
validation, or the difference between current and historical data.

List locally configured profiles without contacting Google, then select the exact URL or origin:

```sh
"$RUNDESK_SKILLS/google-crux/scripts/google-crux" profiles
"$RUNDESK_SKILLS/google-crux/scripts/google-crux" current \
  --profile example --url https://www.example.test/page --form-factor phone
"$RUNDESK_SKILLS/google-crux/scripts/google-crux" history \
  --profile example --origin https://www.example.test --periods 25
```

`--url` reports one page when that page is eligible. `--origin` aggregates every eligible page
under that exact scheme and host. Never substitute origin data for a page that has no record.

Both commands default to the three Core Web Vitals: Largest Contentful Paint, Interaction to Next
Paint, and Cumulative Layout Shift. They report p75 by default. Add `--distributions` only when the
good, needs-improvement, and poor shares are needed; each bucket remains labeled by Google's exact
bounds instead of by an inferred category.

`current` is the latest daily 28-day rolling window. `history` returns weekly snapshots of
overlapping 28-day windows, with 25 periods by default and at most 40. Treat it as a trend, not 25
or 40 independent samples. Missing historical eligibility is reported as an empty value with
`eligible=false`, never as zero.

State the requested and returned identifier, form factor, first and last collection dates, metric,
unit, and eligibility with every conclusion. CrUX represents only eligible Chrome users on public
pages with enough samples; absence is not evidence that a page had no visitors or good performance.

This package is read-only. It cannot run Lighthouse, fetch page content, change a site, or configure
a Google Cloud project. Read `references/sources.md` when auditing or changing its Google API
contract.
