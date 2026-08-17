# Google Integration Environments

Google integrations separate package runtime, OAuth configuration, and mutable state. This keeps
catalog updates safe and lets owners grant each service independently.

## Package runtime

The complete runtime for an integration lives under `skills/<name>/`: its launcher, Python support
code, references, and offline tests. Launchers resolve support files relative to their own location
and never rely on the caller's working directory or a sibling skill.

Packages use Python 3.9+ and the standard library. Installing this catalog creates no virtual
environment, runs no setup script, and installs no dependency. Do not create a shared Google
runtime. If a future package needs a dependency, wait for declarative per-skill runtime support or
ship a self-contained executable with explicit owner approval.

## OAuth configuration and profiles

Each package declares genuinely required values in `rundesk.json`. Rundesk-managed credentials are
injected into the command process and are not written into the package.

The plain declared variable belongs to the default profile. A named profile uses Rundesk's
`<DECLARED_NAME>__<PROFILE>` form. The double underscore is the profile separator; declared names
must match `^[A-Z][A-Z0-9_]*$` and cannot contain `__`. A named profile never falls back to a plain
value because that could combine credentials from different Google identities or OAuth clients.
Likewise, a profile is resolved entirely from Rundesk's suffix form or entirely from a package's
documented legacy infix form. A partial profile in one form is reported as incomplete; fields from
the other form never fill its gaps.

Commands resolve configuration in this order:

1. variables already present in the command process;
2. the command's explicit `--env-file`;
3. the package-specific environment-file variable documented by that package;
4. `${XDG_CONFIG_HOME:-$HOME/.config}/rundesk/integrations/<skill>/env`;
5. the legacy `${XDG_CONFIG_HOME:-$HOME/.config}/<skill>/env`, when supported by the package.

There is no catalog-wide Google token file and no implicit cross-package fallback. If two packages
use the same Google identity, configure each package explicitly. This preserves independent scopes,
rotation, revocation, and removal.

Credential files must be owner-readable only. Commands warn about broad permissions and never print
secrets, authorization headers, refresh tokens, or raw dotenv content.

## Cache and mutable state

Use `${XDG_CACHE_HOME:-$HOME/.cache}/rundesk/integrations/<skill>/` for disposable cache data and
`${XDG_STATE_HOME:-$HOME/.local/state}/rundesk/integrations/<skill>/` only for non-secret durable
operational state. OAuth credentials and grants remain configuration, not cache or state.

Nothing mutable belongs beneath the installed catalog tree. Rundesk may replace that tree during an
update.

## Package requirements

Each package provides credential-free `--help`, an offline `profiles` command, explicit profile and
resource selection, bounded reads, compact text output, and opt-in JSON. Tests use synthetic OAuth
and API fixtures and never contact Google.
