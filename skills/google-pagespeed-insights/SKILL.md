---
name: google-pagespeed-insights
description: Use when the user needs a current Google PageSpeed Insights or Lighthouse assessment for a specific public webpage, including performance, accessibility, best-practices, or SEO scores and prioritized audit findings. It supplies bounded read-only evidence through the PageSpeed Insights API. Do not use for Search Console data, Analytics data, private-page testing, continuous real-user Core Web Vitals history, or changing a website.
---

# Google PageSpeed Insights

Run `$RUNDESK_SKILLS/google-pagespeed-insights/scripts/google-pagespeed-insights`; it resolves the
API key itself, so never inspect or print its source. Read `references/cli.md` only for setup,
environment keys, complete output fields, API behavior, or validation.

List local profiles before analysis. Never guess a profile when more than one is available:

```sh
"$RUNDESK_SKILLS/google-pagespeed-insights/scripts/google-pagespeed-insights" profiles
"$RUNDESK_SKILLS/google-pagespeed-insights/scripts/google-pagespeed-insights" analyze \
  --profile <profile> --url https://www.example.test/ --strategy mobile
```

Default to mobile and the performance category. Add only categories relevant to the question and
keep audit findings bounded:

```sh
"$RUNDESK_SKILLS/google-pagespeed-insights/scripts/google-pagespeed-insights" analyze \
  --profile <profile> --url https://www.example.test/ \
  --category performance --category seo --audit-limit 10
```

Treat Lighthouse scores as a point-in-time lab assessment. Results can vary with page content,
network conditions, Lighthouse versions, and server load. Report the tested URL, strategy,
categories, fetch time, and API-provided Lighthouse version with findings.

This package is read-only. It cannot change a webpage, hosting configuration, Search Console
property, Analytics property, or Google Cloud project.
