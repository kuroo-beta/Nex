# Nex CodeX

A functional Python Discord bot with `Nx` prefix commands, **Components V2** interfaces,
SQLite persistence, one registered bot per user per guild, and confirmed moderation.
There is no website or dashboard. All Discord calls use **discord.py 2.6.4**.

## Quick start

Requires Python **3.12+** (CI covers 3.12, 3.13 and 3.14).

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
# Edit .env privately, then:
python bot.py
```

Alternatively, use `uv sync --frozen --extra dev` and `uv run bot.py`. `pyproject.toml`
pins direct dependencies; `uv.lock` pins the resolved dependency graph. Run from the
repository root so `.env`, the database path and emoji asset directory resolve consistently.
The installed entry point `nex-codex` is also available. No real credentials are
needed for tests. Startup rejects missing or invalid required configuration.

### Discord Developer Portal

1. Create an application and bot in the [Developer Portal](https://discord.com/developers/applications).
   Name it **Nex CodeX** and copy its **Application ID** into `BOT_CLIENT_ID`.
2. Store its bot token privately as `DISCORD_TOKEN`. **Never share a bot token** or
   commit `.env`. This bot never requests another bot's token.
3. Under **Bot → Privileged Gateway Intents**, enable **Message Content**, **Server Members**,
   and **Presence Intent**. Discord approval may be required for verified/large bots.
   Message Content reads prefix commands; Members detects bot arrivals/departures and
   builds member caches; Presence supports conservative offline status checks.
4. Install Nex using the `bot` OAuth2 scope. Grant View Channels, Send Messages,
   Send Messages in Threads (where used), Manage Roles, Kick Members and Ban Members
   only where those features are wanted. Nex does not require Administrator,
   Manage Messages, or Embed Links. External emoji permission may be relevant to
   guild emoji use; this project uses application emojis with Unicode fallbacks.
5. Create a **Bot Adder** role and configure its ID. Place Nex's highest role above
   it and above targets Nex is expected to moderate. Discord requires **Manage Server**
   (`manage_guild`) for the human authorizing a bot installation. The configured Bot
   Adder role must grant it if inviters otherwise lack it. This is a deliberate
   temporary privilege: configure the role narrowly and trust guild members accordingly.
   Nex never edits that role's permissions or assigns Administrator.

See the [Discord component reference](https://discord.com/developers/docs/components/reference)
and [discord.py 2.6.4 API](https://discordpy.readthedocs.io/en/v2.6.4/api.html).
`LayoutView`, `Container`, `TextDisplay`, `ActionRow`, buttons and string selects are
real library types. discord.py automatically sets `IS_COMPONENTS_V2` on these layouts.
Messages contain no legacy embeds; mentions are disabled. Controls are requester-bound,
expire, and disable themselves when the message is still editable. After a restart,
old confirmation controls are inert; run the command again.

## Configuration

Comma-separated ID lists use numeric Discord IDs; enable Developer Mode to copy them.

| Variable | Meaning |
| --- | --- |
| `DISCORD_TOKEN` | Required private Nex bot token; never logged or stored in SQLite |
| `BOT_OWNER_ID` | Required owner user ID |
| `DEVELOPER_IDS` | IDs authorized for developer operations |
| `CODE_USER_IDS` | Additional IDs allowed to use `Nx code` |
| `BOT_ADDER_ROLE_ID` | Required assignable temporary role ID; the configured role must exist in a guild to enable invitations there |
| `BOT_ADDER_ROLE_IDS` | Optional per-guild role overrides: `guild_id:role_id,guild_id:role_id`; fallback is `BOT_ADDER_ROLE_ID` |
| `BOT_CLIENT_ID` | Required Nex application ID, checked against the authenticated application |
| `SUPPORT_SERVER_URL` | Optional public HTTPS support link; empty reports unconfigured |
| `PUBLIC_REPOSITORY_URL` | Optional public HTTPS source link; never use a private/signed URL |
| `INVITE_PERMISSIONS` | Permissions requested for the **invited** bot, default `0`; Administrator is rejected |
| `INVITE_TIMEOUT_SECONDS` | Role/reservation lifetime, default **20**, permitted range 1–300 |
| `PURGE_COOLDOWN_SECONDS` | Per-guild purge cooldown, default **10800** (3 hours) |
| `DATABASE_URL` | `sqlite+aiosqlite:///data/nex_codex.db`; relative to working directory; four slashes allow an absolute path |
| `PROTECTED_USER_IDS` | Users excluded from moderation |
| `PROTECTED_BOT_IDS` | Bots excluded from removal, purge and moderation |
| `PROTECTED_GUILD_IDS` | Guilds excluded from destructive moderation, purge and serverleave |
| `GLOBAL_PURGE_GUILD_IDS` | Explicit global purge guild allowlist; empty disables global purge and hides it from help |
| `GLOBAL_CONCURRENCY` | Concurrent cross-guild operations, default 3, maximum 10 |
| `APPLICATION_EMOJI_PROVISIONING` | Opt-in uploads, default `false` |
| `EMOJI_ASSET_DIRECTORY` | Optional asset path, default `assets/emojis` |

Public links must use HTTPS, have no embedded credentials, query string or fragment.
Operators must only configure genuinely public links. The bot cannot determine whether
an arbitrary HTTPS resource is confidential. No source packaging or file download
feature exists, so secrets, database files and machine paths cannot be attached by `Nx code`.

### Authorization policy

- Public: ping, support, invite, list, help; remove is limited to the registered owner
  or a configured developer/owner.
- Owner and developers: all administration/moderation commands, with the code exception below.
- Trusted users: only the explicitly granted `purge`, `ban`, or `ignore` privilege,
  in the guild where it was granted. Discord Administrator/Manage Server alone does
  **not** grant Nex administrative command access.
- `Nx code`: **only** IDs listed in `DEVELOPER_IDS` or `CODE_USER_IDS`. The owner must
  also be explicitly listed to use this command.
- Owner, developers, Nex itself and each guild owner are protected against moderation.
- Every privileged handler checks authority server-side; destructive callbacks recheck
  authority and protections when confirmed. Help visibility does not grant permission.
- Ordinary messages, other bots and webhooks cannot invoke commands. Prefixes `Nx`, `nx`,
  `NX`, and `nX` followed by whitespace work. `Nxping` and text containing `Nx` later
  in the message do not invoke anything. Missing/extra arguments return usage guidance.

## Command reference

Use `Nx help` for category selection, command selection and pages; `Nx help <command>`
shows exact usage, including subcommands. IDs below mean numeric Discord snowflakes.

| Command | Behavior / access |
| --- | --- |
| `Nx ping` | Gateway latency and initialization health; response timing is not measured |
| `Nx support` | Configured support link or explicit unconfigured message |
| `Nx invite <bot_id>` | Public; atomically reserve a slot and temporarily assign Bot Adder |
| `Nx list` | Public; paginated current-guild registrations, owners, dates and status |
| `Nx remove <bot_id>` | Registered owner or developer; confirm kick and registration release |
| `Nx purge` | Developer or trusted `purge`; preview/confirm offline registered bots only |
| `Nx help [command]` | Dynamic authorization-aware Components V2 help |
| `Nx globalban <user_id>` | Developer; confirm bans in all eligible connected guilds, including absent targets |
| `Nx globalkick <user_id>` | Developer; confirm kicks only where target is present; never bans |
| `Nx globalunban <user_id>` | Developer; confirm unban attempts with independent per-guild outcomes |
| `Nx serverleave <guild_id>` | Developer; confirm leaving exactly one unprotected guild |
| `Nx globalpurge` | Developer; explicit configured guild scope only, preview/confirmation required |
| `Nx trusted list` | Developer; list this guild's trusted grants |
| `Nx trusted add <user_id> <purge\|ban\|ignore>` | Developer; grant one guild-scoped privilege to a current member |
| `Nx trusted remove <user_id> <purge\|ban\|ignore>` | Developer; revoke one privilege; never changes configured owner/developers |
| `Nx whitelist list` | Developer; list current-guild and global protection entries |
| `Nx whitelist add <bot\|user\|guild> <target_id> [guild\|global]` | Developer; add protection (default scope: this guild) |
| `Nx whitelist remove <bot\|user\|guild> <target_id> [guild\|global]` | Developer; remove the specified persisted protection |
| `Nx ban <user_id>` | Developer or trusted `ban`; confirm a **current-guild** ban, never a global ban |
| `Nx ignore add <channel_id>` | Developer or trusted `ignore`; validate membership in this guild |
| `Nx ignore remove <channel_id>` | Same access; remove even stale/deleted channel records |
| `Nx ignore list` | Same access; list current-guild settings |
| `Nx code` | Explicit code-authorized IDs only; public repository link or configuration message |

Seven additional developer commands have no specification and are intentionally
unimplemented and unadvertised. No invented commands are included.

### Protection and ignored channels

Whitelist `bot` protects against purge, removal and moderation; `user` protects
against those actions for that ID; `guild` protects the identified guild against
moderation, purges and leaving. Scope `guild` applies the entry only in the invoking
guild; use `global` to protect a guild throughout the application. Configuration
protections are independent of persisted entries and cannot be removed with commands.

Ignored channel records are guild-scoped and match exact channel/thread IDs.
Ordinary prefix commands receive no response there. Developers/owner bypass ignores;
trusted users with `ignore` can still run the ignore controls to recover settings.
Component callbacks already sent to a channel retain their normal requester and
permission checks. Parent channel settings do not automatically include threads.

## Registration and restart behavior

SQLite enforces unique `(guild_id, owner_id)` **and** `(guild_id, bot_id)` across pending
and active records. A transaction with `BEGIN IMMEDIATE` reserves the slot. The same
owner may register one bot in each of several guilds; different owners may each
register one bot in the same guild. Pending invites occupy the slot until activation,
failure or expiry. Duplicate bot targets and bots already present are rejected.

Invitations generate real OAuth2 links without collecting tokens. The link's client ID
must identify the **target bot** (a bot user's ID is its application ID), rather than
Nex's `BOT_CLIENT_ID`. This is required by Discord's OAuth2 API. The guild is fixed in
the link. Creating a link does **not** add a bot: Discord must confirm membership.
An unexpired reservation activates only after the expected target is fetched as a bot
in the correct guild and the inviter is still present. Guild membership alone cannot
prove which human completed OAuth; there is no claimed OAuth callback or attribution.

Temporary role ownership is written before assignment. A pre-existing role is never
removed. Cleanup keeps an owned role while any unexpired matching reservation requires
it. Completion, failure, departure or expiry releases the lease when no session needs
it. Forbidden/failed cleanup keeps a durable lease and retries after permissions recover.
A role removed and manually re-added during an owned lease cannot be distinguished from
the original assignment; avoid manual role changes during the 20-second window.

On startup the database schema initializes, cogs load, Discord authenticates, application
emojis initialize, and pending sessions/leases recover before commands become available.
Transient guild recovery failures are logged and retried by maintenance. A two-second
maintenance loop expires reservations and cleans roles; exact execution time may be later
than expiry due to Discord rate limits or outages. Active registrations are reconciled at
startup and approximately every minute; only a confirmed REST 404 releases an absent
bot's slot. An offline member is still a member and remains registered. When a bot leaves,
its registration is released. An owner's departure cancels their pending invitation;
their active bot keeps its registration until the bot leaves or a developer removes it.

Run **one Nex process per database/application**. Guild locks serialize invitations,
removals, purges and moderation within that process. Purge cooldowns persist and are
claimed before any kick; crashes do not reset them. A restarted operation must be
previewed/confirmed again. There is no persistent automatic replay of destructive actions.

Shutdown cancels and awaits maintenance, closes Discord HTTP/gateway and closes SQLite.
Durable pending sessions and role leases survive a shutdown and resume on restart. A
stopped bot cannot remove roles on Discord: restart promptly after outages. If Nex is
removed from a guild, the operator must handle any remaining roles there; inaccessible
guild records are retained so they can be recovered on a later rejoin. Explicit serverleave
cancels pending sessions and attempts role cleanup **before** leaving; a failed cleanup
prevents the intentional leave.

## Purge and moderation safety

Purge only considers registered bots, fetched in the current guild, whose complete
gateway member/presence cache reports **offline**. Online, unknown, protected, ordinary
users and unregistered bots are skipped. REST member responses do not contain presence;
status is unknown when intents, member cache completeness, gateway readiness or guild
availability are missing. Presence is Discord-reported state, not a bot uptime probe;
invisible bots may appear offline. The preview discloses skips and every kick is
revalidated at execution time. Discord itself enforces final permission/hierarchy checks.

Each eligible bot is kicked separately. Registration deletion follows a successful kick;
a confirmed absent member may also release a slot and is reported as skipped/absent.
Failed API calls retain registration state. Cross-guild operations use bounded concurrency,
respect protection and report independent outcomes; discord.py handles Discord rate limits.
Global purge uses exactly the configured allowlist and the same per-guild locks, protections,
registration rules and cooldowns as local purge. Developers are authorized to purge all
eligible registrations in those guilds; trusted privileges never grant global operations.
If storage fails after Discord confirms an action, the response reports possible partial
completion. Reconciliation repairs missing registrations; audit recording cannot be
guaranteed while storage itself is unavailable.

## Application emoji provisioning

Optional assets live in `assets/emojis/`: `success.png`, `error.gif`, etc. Supported
logical names appear in `nex/services/emoji.py`. PNG/GIF/JPG files must be valid images
at most 256 KiB. No images are required to start the bot.

Nex calls `fetch_application_emojis` and matches normalized names before considering an
upload. Existing emojis are reused with uploads disabled. Set
`APPLICATION_EMOJI_PROVISIONING=true` to opt into `create_application_emoji`. Application
emoji routes use Nex's authenticated bot/application; guild Manage Expressions permissions
do not grant application ownership. The API determines actual access and capacity.
Use the matching application/token; configure application emojis in the Portal where
available. Listing/upload failures, limits, invalid files and missing assets produce
safe diagnostics and Unicode fallbacks, never fake upload success or a startup failure.
Uploads happen once per process initialization; reconnects do not duplicate them.
See [application emoji API](https://discord.com/developers/docs/resources/emoji).

## Testing and linting

```bash
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
```

Tests use synthetic IDs and mocked Discord APIs; no token, real server, screenshot or
external service is required. Coverage includes prefix parsing, strict arguments,
authorization boundaries, concurrent SQLite reservations (including independent
connections), guild isolation, invitation expiry/restart/role ownership, purge
revalidation/cooldown/error handling, ignored channels, protections, secret-safe code
links and actual Components V2 serialization. CI runs the suite on supported Python
versions. `audioop` may produce a dependency deprecation warning on Python 3.12; this bot
does not use voice.

For an optional live integration smoke test, use a **separate test application and guild**:
enable intents, verify `Nx ping`/help components, invite a disposable bot, observe role
expiry and restart recovery, then test confirmed removal and a protected purge preview.
Never test global moderation with a production-connected bot. No live integration
success is claimed by the unit suite.

## Deployment, storage and retention

Use a private `.env` or environment injection, a persistent writable `data/` directory,
and a service manager that restarts on failure. Install dependencies before starting;
run `python bot.py` from the project directory. Use a dedicated unprivileged OS user and
`umask 077`. Send SIGTERM for clean shutdown; do not launch overlapping instances.
Back up SQLite with its backup API or stop the bot before copying the database. Preserve
the database and WAL files together during filesystem snapshots. Test restored backups
privately before production use. Do not commit/upload the live database.

Schema version 1 is tracked in `schema_version`; unsupported versions are rejected.
Registrations/reservations, role leases, trusted privileges, protection entries, ignored
channels, cooldowns and audits persist in SQLite using string Discord IDs and parameterized
SQL. No tokens, environment contents or message text are stored. Completed/expired sessions
are removed after their state transitions; durable role leases remain until cleanup can
finish. Active registration rows are deleted on successful removal/confirmed absence;
audit rows preserve actor, guild, target, result and timestamp. Audit/configuration records
have **no automatic retention expiry**. Restrict database access, choose a local retention
policy and prune/back up audit records during operator-controlled maintenance. No command
exports audit or live user data.

### Wispbyte / panel hosting startup

For panels using `/usr/local/bin/python /home/container/${PY_FILE}` and
`pip install --prefix .local`, the launcher `start.py` provides an alternative:
it creates a private `.venv`, installs the pinned `requirements.txt` there, and
replaces itself with the bot process. Failed installation stops startup. It uses
no bot token during installation and does not clone or overwrite uploaded files.

Configure the host before restarting:

1. Select a **Python 3.12+** image/interpreter. The supplied console log used
   **Python 3.11.17**, which does not meet this project's existing requirement.
   Both entry points now report that mismatch before importing dependencies.
2. If you uploaded the project files and the directory has no `.git`, set
   **`USER_UPLOAD=1`**. This skips the startup command's duplicate attempts to
   clone into the nonempty upload directory. If using Git deployment instead,
   have the host initialize the checkout in a separate empty directory; preserve
   `.env` and the database when moving files.
3. Set **`PY_FILE=start.py`**, clear **`PY_PACKAGES`**, and set
   **`REQUIREMENTS_FILE=panel-requirements-disabled.txt`**. Do **not** create that
   file: the panel command shown in the log only runs its `.local` installer if
   the configured file exists. Keep the real `requirements.txt` in the project;
   `start.py` installs it into `.venv`. These settings bypass the failing
   `--prefix .local` step; changing requirements alone cannot repair ownership.
4. Ensure the server user can write the project directory (for `.venv`) and the
   database directory (`data/` by default). If ownership is wrong, ask the host
   to restore it to the server user. Do not use `chmod 777` or run the bot as root.
5. Provide your private `.env`/environment configuration and start the server.
   If the image does not include `venv`/`ensurepip`, ask the host to enable those
   Python modules. After changing the Python image, stop the bot and move the old
   `.venv` aside before starting again.

If the host allows a custom command, use `python3.12 start.py` directly instead
of the panel's automatic clone/install command. With an already prepared virtual
environment you can continue running `.venv/bin/python bot.py` directly.
The launcher repairs dependency preparation; it cannot change the panel's image,
file ownership, or Discord configuration. A pip cache ownership warning is separate
from the fatal `.local` permission error; the launcher disables pip's cache.

### Known Discord limits

- Prefix parsing requires Message Content intent; Presence/Member intents need Portal setup.
- REST cannot supply reliable online/offline presence; unknown status is never purged.
- Nex cannot install another bot by itself or guarantee the invite is accepted in 20 seconds.
- Bot installations require human OAuth approval and Manage Server; role hierarchy still applies.
- Roles belong to individual guilds. For multi-guild invitations, configure each guild
  in `BOT_ADDER_ROLE_IDS`; the single `BOT_ADDER_ROLE_ID` is the fallback.
- Application emoji access/capacity are controlled by Discord, not guaranteed by a token alone.
- Components require Discord client support. Deleted/inaccessible messages cannot be updated.
- Leaving the invoking guild prevents posting a result there after departure; consult the local audit record.
- Permission changes, network outages and rate limits can cause partial operations; results
  and audits distinguish confirmed outcomes from failures/skips.
- Seven unspecified developer commands and source-package generation are not implemented;
  global purge is disabled until its guild scope is explicitly configured.
