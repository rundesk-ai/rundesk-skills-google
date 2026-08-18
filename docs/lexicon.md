# Google integration lexicon

Register: operator. Commands and messages are concise, factual, and optimized for repeated use.

This lexicon records recurring concepts and boundary mappings. It does not inventory every API
field, report dimension, metric, or command option.

## Shared concepts

### Profile

A named set of credentials for one Google identity. A profile is not a Google Account, Analytics
account, Search Console property, or Cloud project. Commands report the selected profile alongside
the resource being queried.

Use `profile` in commands, documentation, and output. Avoid `connection`, `login`, and `account`
when the value specifically identifies Rundesk credential configuration.

### Google identity

The user or service account represented by a profile. Access is determined by both the OAuth scope
and the permissions granted to this identity on the requested Google resource.

## Search Console

### Site

The API's `siteUrl` resource, including URL-prefix and domain properties. Use `site` in commands to
match the Search Console API. Display the exact API value because prefixes and trailing slashes are
significant identifiers.

### Search performance

Search Console query, page, country, device, date, and search-appearance results returned by Search
Analytics. Use `performance` for the command and report concept. Avoid generic `analytics`, which is
ambiguous with Google Analytics.

A Search Console date is a Pacific reporting day, not a UTC or local one. Say `Pacific reporting
day` when the distinction affects which days count as complete.

### URL inspection

Google's indexed-state inspection for one URL under one Search Console site. Use `inspect-url` for
the operation. Do not shorten the resource to `page` because the API requires a complete URL.

## Google Analytics

### Analytics account

The top-level Google Analytics administrative container. Use `account` only within the Analytics
skill where the product boundary is already explicit.

### Analytics property

A GA4 reporting and configuration resource. Use `property` and the exact numeric property ID. Do
not call it a `site`; one property can receive data from several web or app streams.

### Report

A bounded result from the Google Analytics Data API. Use `report` for historical data and
`realtime` for the Realtime API. Preserve Google-defined metric and dimension names verbatim at the
API boundary.

### Breakdown

The dimension a bounded report groups by, named for the question rather than the API field, such as
`channel`, `country`, or `brand`. Use `breakdown` for the command option and the concept. It is not
a segment, a filter, or a comparison; it only chooses the grouping the package already supports.

### Key event

An event a property marks as significant, and the name Google adopted in 2024 for what it
previously called a conversion. Use `key event` in commands, documentation, and output, including
for lead measurement. Say `conversion` only when quoting older Google material, and never mix the
two in one sentence.

### Item

A product in Google Analytics ecommerce measurement. Use `item` for the API-facing concept and
`product` only in prose about a shop. An item exists in Analytics because the site sent an ecommerce
event about it; it is not a Merchant Center product and carries no feed, price, or availability.

## PageSpeed Insights

### Analysis

A point-in-time Lighthouse assessment returned by the PageSpeed Insights API for one public URL.
Use `analyze` for the command and `analysis` for the result. Do not call it a report, which is the
established Google Analytics concept.

### Strategy

The PageSpeed Insights device-emulation choice. Use lowercase `mobile` and `desktop` in commands,
documentation, and output; the API's own query enums are uppercase `MOBILE` and `DESKTOP`, and the
command maps to them at the request boundary. Do not call this a device because it does not identify
a physical device.

### Audit

One Lighthouse diagnostic or opportunity within an analysis. Use `audit` for the individual result
and `category` for Google's performance, accessibility, best-practices, and SEO groupings. Those
lowercase names are the Lighthouse result keys; the query enums are `PERFORMANCE`, `ACCESSIBILITY`,
`BEST_PRACTICES`, and `SEO`. Keep the lowercase form everywhere except the request itself.
