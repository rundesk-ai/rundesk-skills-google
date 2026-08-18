# google-auth CLI reference

## Commands

```text
google-auth provider [--json]
google-auth accounts [--profile <app-profile>] [--auth] [--json]
google-auth login [--profile <app-profile>] [--json]
```

`provider` prints the declaration in `oauth-provider.json`; `--json` prints it verbatim. `accounts`
asks Rundesk which Google accounts it holds for one app profile, which reads sealed local state and
reaches no Google API. `--auth` signs in first. `login` runs `rundesk login google` and then lists
what is connected. Errors go to stderr as `ERROR: <message>` with exit 2; nothing here ever prints a
token, a client value, or an authorization header.

## What this package declares

Rundesk supplies the OAuth mechanics — browser, PKCE, token exchange, refresh, and the sealed grant
store — and reads Google's particulars from `oauth-provider.json`:

| Declared | Value |
|---|---|
| `authorization_endpoint` | `https://accounts.google.com/o/oauth2/v2/auth` |
| `token_endpoint` | `https://oauth2.googleapis.com/token` |
| `identity_endpoint` | `https://openidconnect.googleapis.com/v1/userinfo` |
| `identity` | `sub` is the durable account key; `email` is the human selector; `email_verified` must be true |
| `base_scopes` | `openid`, `email` |
| `authorization_parameters` | `access_type=offline` for a refresh token, `prompt=consent select_account` so the account is chosen deliberately |
| `client_secret` | `true`: Google issues one even for a desktop client |
| `capabilities` | `analytics`, `search-console`, and `merchant`, each naming one Google scope |

A declaration may not set `client_id`, `redirect_uri`, `response_type`, `scope`, `state`,
`code_challenge`, or `code_challenge_method`: those belong to the mechanics and Rundesk refuses a
declaration that names them. Adding a Google API here means adding one capability and its scope, and
nothing else.

## Set up the Google Cloud project

1. Use a dedicated Google Cloud project and enable only the APIs this installation will use: Google
   Analytics Data and Admin, Search Console, and Merchant.
2. Configure Google Auth Platform branding and audience. Internal suits a single eligible Workspace
   organization; otherwise choose External and add every intended account as a test user while the
   app is in Testing.
3. Under Data Access, declare `openid`, `email`, and only the capability scopes above that this
   installation needs.
4. Under Clients, create an OAuth client whose application type is **Desktop app**. Do not create a
   Web application client and do not add a public redirect URI: Rundesk's callback is a temporary
   `http://127.0.0.1:<random-port>/<random-path>` loopback, which a desktop client permits without a
   registered redirect URI.
5. Connect an account. Rundesk asks for the client values it does not yet have, stores them sealed,
   and never passes them to a skill:

   ```sh
   rundesk login google
   rundesk login google --profile acme
   ```

   The second form is a separate OAuth app profile, for a second Google Cloud project or a client
   belonging to someone else.

## Merchant's scope is broader than the reading it does

Google publishes exactly one Merchant API scope and it is read-write. Constrain the signed-in
identity in Merchant Center instead, as `skills/google-merchant/references/cli.md` describes.

## When a grant stops working

An External consent screen left in **Testing** issues refresh tokens that expire in seven days for
these scopes; publish the app or expect to reconnect. Revocation, inactivity, Workspace policy, and
Google's per-account token limits can also end a grant. Every Google command here reports what
Rundesk refused and names the exact `rundesk login google` to repeat, with the profile when one is
in use.

## Validation

```sh
python3 skills/google-auth/scripts/google-auth.d/test-google-auth.py -q
skills/google-auth/scripts/google-auth --help
skills/google-auth/scripts/google-auth provider
```

Tests are offline: a stand-in Rundesk answers the bridge exactly as the real one documents it,
including its refusal of any response descriptor that is not a connected unnamed local socket.

## Official references

- [Google OAuth 2.0 for desktop apps](https://developers.google.com/identity/protocols/oauth2/native-app)
- [OpenID Connect on Google](https://developers.google.com/identity/openid-connect/openid-connect)
- [Google API scopes](https://developers.google.com/identity/protocols/oauth2/scopes)
