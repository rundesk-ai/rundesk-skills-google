# Brief — rundesk-skills-google

*What this catalog is and why it exists. One screen, and it changes when the catalog does.*

## Story

`rundesk-skills-google` gives a Rundesk agent guarded access to Google's measurement services —
Search Console, Analytics, PageSpeed Insights, Chrome UX Report, and Merchant Center. OAuth-backed
packages use Rundesk-managed Google sign-in; public measurement packages use scoped API keys. Each
package ships its own command, operating guidance, authorization declaration, and offline tests.

One sign-in serves the three OAuth packages, and `google-auth` owns it. PageSpeed and CrUX use
separate API keys because they query public measurements without a Google identity.

## Why it exists

These five services answer the questions a marketing or growth agent actually gets asked: what is
indexed, what is being searched for, how the site is used, how it performs in the lab and for real
users, and what the feed says. Each has its own API, quotas, and vocabulary.

Putting them in one catalog means an agent configures each authorization boundary once, and a term
means the same thing across all five — which is what `concepts/lexicon.md` exists to hold.

## Users

- Rundesk agents doing search, growth, analytics, or merchandising work with first-hand data.
- The owner, granting one service at a time under a sign-in Rundesk manages rather than a credential
  copied into a repository.

*Sourced from the readme, the environments contract, and the package contract.*

## Scope

- **Covers:** Search Console, Analytics, PageSpeed Insights, Chrome UX Report, and Merchant Center,
  each behind its own guarded command; shared Google sign-in where OAuth applies; and the OAuth,
  configuration, cache, and state contract in `ENVIRONMENTS.md`.
- **Refuses:**
  - A general Google API client. Every call goes through a package that declares what it reaches.
  - Holding a credential in the repository. Sign-in is Rundesk-managed and declared, never copied.
  - Inventing a term. A concept that crosses two services is defined once in the lexicon.
  - General method the default catalog owns, and non-Google services that belong to another
    integration catalog.

## External systems

- Google Search Console, Google Analytics, PageSpeed Insights, Chrome UX Report, and Merchant Center.
- Google OAuth — the sign-in Rundesk manages on the owner's behalf, per `ENVIRONMENTS.md`.
- Rundesk — installs this catalog, holds the authorization, and grants packages per agent.
- GitHub — hosts the repository and serves the release a catalog install fetches.
