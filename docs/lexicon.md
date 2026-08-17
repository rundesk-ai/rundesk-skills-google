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

### Change history

Administrative changes returned by the Analytics Admin API. Use `change-history`; avoid vague names
such as `updates` or `activity`.
