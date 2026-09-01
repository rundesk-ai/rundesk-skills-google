# Google Analytics sources

Official Google sources govern this package's field names, requests, measurement guidance, and
interpretation boundaries. Check them again when Google changes the Data API or GA4 terminology.

| Contract | Primary source |
|---|---|
| Accounts and properties | [Admin API account summaries](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list) |
| Historical and realtime reports | [runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport), [runRealtimeReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runRealtimeReport) |
| Dimensions, metrics, scopes, and compatible names | [Data API schema](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema), [predefined reports](https://developers.google.com/analytics/devguides/reporting/data/v1/predefined-reports) |
| Property-specific and custom fields | [getMetadata](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/getMetadata) |
| Core report field combinations | [checkCompatibility](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/checkCompatibility) |
| Filters, ordering, metadata, quotas | [FilterExpression](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/FilterExpression), [OrderBy](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/OrderBy), [ResponseMetaData](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData), [limits and quotas](https://developers.google.com/analytics/devguides/reporting/data/v1/quotas) |
| Ordered funnel request and response | [runFunnelReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1alpha/properties/runFunnelReport), [funnel reporting guide](https://developers.google.com/analytics/devguides/reporting/data/v1/funnels) |
| Key-event terminology | [Data API changelog](https://developers.google.com/analytics/devguides/reporting/data/v1/changelog) |
| Search and onsite measurement boundary | [Using Search Console and Analytics for SEO](https://developers.google.com/search/docs/monitor-debug/google-analytics-search-console) |
| Acquisition and attribution scopes | [Traffic-source dimensions](https://support.google.com/analytics/answer/11080067), [default channel group](https://support.google.com/analytics/answer/9756891) |
| Event setup, recommended names, and limits | [Set up events](https://developers.google.com/analytics/devguides/collection/ga4/events), [recommended event reference](https://developers.google.com/analytics/devguides/collection/ga4/reference/events), [recommended-event guidance](https://developers.google.com/analytics/devguides/collection/ga4/reference/recommended-events), [event collection limits](https://support.google.com/analytics/answer/9267744) |
| Server and offline event association | [Measurement Protocol use cases](https://developers.google.com/analytics/devguides/collection/protocol/ga4/use-cases), [send Measurement Protocol events](https://developers.google.com/analytics/devguides/collection/protocol/ga4/sending-events) |
| Ecommerce events and validation | [Measure ecommerce](https://developers.google.com/analytics/devguides/collection/ga4/ecommerce), [validate ecommerce](https://developers.google.com/analytics/devguides/collection/ga4/validate-ecommerce), [transaction IDs](https://support.google.com/analytics/answer/12313109), [ecommerce metrics](https://support.google.com/analytics/answer/13428834) |
| Collection verification | [Troubleshoot event collection](https://developers.google.com/analytics/devguides/collection/ga4/troubleshoot) |
| Cross-domain, referral, and consent implementation | [Cross-domain measurement](https://developers.google.com/tag-platform/devguides/cross-domain), [unwanted referrals](https://support.google.com/analytics/answer/10327750), [consent mode](https://developers.google.com/tag-platform/security/guides/consent) |
| Freshness and modeled key-event changes | [Data freshness](https://support.google.com/analytics/answer/11198161), [key-event processing](https://support.google.com/analytics/answer/10596866) |
| OAuth authorization | [OAuth 2.0](https://developers.google.com/identity/protocols/oauth2), [OAuth Playground](https://developers.google.com/oauthplayground/) |
