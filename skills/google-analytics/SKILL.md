---
name: google-analytics
description: Use when the user needs to inspect Google Analytics 4 accounts or properties or analyze bounded historical or realtime GA4 metrics. It supplies read-only GA4 discovery and reporting through explicitly selected credential profiles and properties. Do not use for Universal Analytics, tag implementation, Google Ads, Search Console, Analytics Admin change history, or Analytics configuration changes.
---

# Google Analytics

Run `$RUNDESK_SKILLS/google-analytics/scripts/google-analytics`; it resolves credentials itself, so
never inspect or print their source. Read `references/cli.md` for setup, environment keys, report
arguments, output fields, or validation.

Start with `profiles`, then discover the accounts and properties visible to the selected identity:

```sh
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" profiles
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" accounts --profile <profile> --limit 25
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" properties --profile <profile> --limit 50
```

Never guess a profile or property. Use the exact numeric property ID returned by `properties`:

```sh
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" report --profile <profile> --property <id> --start-date 28daysAgo --end-date today --metrics sessions,activeUsers --dimensions date --limit 100
"$RUNDESK_SKILLS/google-analytics/scripts/google-analytics" realtime --profile <profile> --property <id> --metrics activeUsers --dimensions country --limit 25
```

Keep dimensions, metrics, date ranges, and row limits no broader than the question requires. Human
output is compact CSV. Use `--json` only for downstream processing or when normalized JSON fields
are explicitly needed.

All commands are read-only. This package cannot create or edit Analytics accounts, properties,
streams, events, audiences, access, or configuration.
