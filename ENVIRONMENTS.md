# Google Integration Environments

Google integrations separate package runtime, sign-in, and mutable state. Rundesk owns Google
sign-in; a package owns its commands. This keeps catalog updates safe and lets owners grant each
service independently.

## Package runtime

The complete runtime for an integration lives under `skills/<name>/`: its launcher, Python support
code, references, and offline tests. Launchers resolve support files relative to their own location
and never rely on the caller's working directory or a sibling skill.

Packages use Python 3.9+ and the standard library. Installing this catalog creates no virtual
environment, runs no setup script, and installs no dependency. Do not create a shared Google
runtime. If a future package needs a dependency, wait for declarative per-skill runtime support or
ship a self-contained executable with explicit owner approval.

## Google sign-in

Rundesk owns Google OAuth. A package that reaches a Google API declares no client ID, client secret,
or refresh token, keeps no grant, and contains no OAuth, browser, refresh, or persistence code. It
asks the install's own CLI for one short-lived access token through Rundesk's hidden, provider-
neutral `_oauth` bridge, naming only the provider and the fixed capability it needs. The answer arrives over one end of an inherited
anonymous local socket pair as a single length-prefixed JSON frame, so a token never reaches an
argument, an environment variable, stdout, stderr, a log, or a file. Rundesk refuses any other
descriptor, including a pipe, a named socket, a regular file, and standard input or output.

An owner connects an account once:

```sh
rundesk login google [--profile <app-profile>]
```

A profile is one OAuth app configuration, not a person, and may hold several verified Google
accounts. Rundesk keys each account by Google's immutable subject identifier and selects it by
email, so every OAuth command takes `--profile` for the app, `--email` for the account, and `--auth`
to run that login first. Each is needed only when more than one answer exists; an ambiguous
selection is refused, and a missing credential or scope is reported with the exact login command to
run. A Rundesk too old to answer the bridge is reported as that, not as a Google failure.

A new Google package uses this same bridge and its capability name. Do not add a second way to
authorize.

Google's own definition — endpoints, identity fields, base scopes, authorization parameters, and one
scope per capability — belongs to this catalog, in `skills/google-auth/oauth-provider.json` beside
that package's `SKILL.md`. Rundesk supplies the mechanics and reads the declaration; no package here
states any mechanics of its own. A declaration may not name `client_id`, `redirect_uri`,
`response_type`, `scope`, `state`, `code_challenge`, or `code_challenge_method`, and Rundesk refuses
one that does. `skills/google-auth/references/cli.md` owns the Google Cloud setup an owner follows
once, so no other repository needs to describe it.

Exactly one installed skill may declare a given provider. Adding another Google API means adding one
capability and its scope to that declaration, not a second declaration.

## Owner-supplied values

A value only an owner can provide, such as an API key, is declared in `rundesk.json`. Rundesk stores
it outside the catalog and injects it into the command process; it is never written into the package.

The plain declared variable belongs to the default profile. A named profile uses Rundesk's
`<DECLARED_NAME>__<PROFILE>` form. The double underscore is the profile separator; declared names
must match `^[A-Z][A-Z0-9_]*$` and cannot contain `__`. A named profile never falls back to a plain
value because that could combine values belonging to different owners.

Commands resolve such configuration in this order:

1. variables already present in the command process;
2. the command's explicit `--env-file`;
3. the package-specific environment-file variable documented by that package;
4. `${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/<skill>/env`;
5. the legacy `${XDG_CONFIG_HOME:-$HOME/.config}/<skill>/env`, when supported by the package.

Configuration files must be owner-readable only. Commands warn about broad permissions and never
print secrets, authorization headers, or raw dotenv content.

## Cache and mutable state

Use `${XDG_CACHE_HOME:-$HOME/.cache}/rundesk/integrations/<skill>/` for disposable cache data and
`${XDG_STATE_HOME:-$HOME/.local/state}/rundesk/integrations/<skill>/` only for non-secret durable
operational state. OAuth credentials and grants remain configuration, not cache or state.

Nothing mutable belongs beneath the installed catalog tree. Rundesk may replace that tree during an
update.

## Package requirements

Each package provides credential-free `--help`, an offline `profiles` command, explicit profile and
resource selection, bounded reads, compact text output, and opt-in JSON. Tests are offline: a
stand-in Rundesk answers the sign-in bridge and synthetic fixtures replace every Google boundary.
