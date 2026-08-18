---
name: google-auth
description: Use when a Google skill in this catalog reports that no account is connected, an app profile is unconfigured, an account choice is ambiguous, or a scope is missing; when the user asks to connect, list, choose, or re-authorize a Google account for Rundesk; or when setting up the Google Cloud OAuth app these skills sign in with. It supplies the catalog's Google provider definition and the sign-in commands the other Google skills depend on. Do not use it to read Analytics, Search Console, Merchant Center, or PageSpeed data.
---

# Google sign-in

Rundesk owns Google sign-in for this catalog. This package owns the definition Rundesk reads —
Google's endpoints, identity fields, base scopes, and one scope per capability — in
`oauth-provider.json` beside this file. It holds no client, no grant, and no OAuth code, and the
other Google skills read nothing from it and never run it.

```sh
"$RUNDESK_SKILLS/google-auth/scripts/google-auth" provider
"$RUNDESK_SKILLS/google-auth/scripts/google-auth" accounts [--profile <app-profile>]
"$RUNDESK_SKILLS/google-auth/scripts/google-auth" login [--profile <app-profile>]
```

`provider` reports what this catalog declares Google to be and contacts nobody. `accounts` lists the
Google accounts Rundesk holds for one OAuth app profile and reaches no Google API. `login` runs
Rundesk's own browser sign-in and then shows what it connected.

A *profile* is one OAuth app configuration, not a person, and one profile can hold several verified
Google accounts. Every data skill here takes `--profile <app-profile>` to pick the app and
`--email <address>` to pick the account, each needed only when Rundesk holds more than one.

When a Google skill says nothing is connected, run `accounts` to see what exists, then ask the owner
to run `rundesk login google` in their own terminal. Use `login` here only when a browser is
available to whoever is running this. Never ask anyone for a client ID, client secret, or refresh
token: Rundesk prompts for and stores the app client values itself, and a skill process cannot read
them.

Read `references/cli.md` for the Google Cloud project setup, the scope each capability carries, and
what to do when a grant expires or is revoked.
