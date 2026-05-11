# Pigeon150m Ingest Service

Pigeon is a Torn API ingest service plus a Discord bot. It stores raw logs in SQLite, deduplicates by `log.id`, and writes item `206` deposits into the ledger and user balance table.

## Runtime Model

The app has two separate processes:

- `pigeon-ingest` polls Torn and writes to SQLite
- `pigeon-bot` connects to Discord and serves slash commands

Locally, that usually means two terminals. On a server, run them as two separate services.

## Local Run

Install the project and run one ingest cycle:

```bash
python -m pip install -e .
pigeon-ingest --once
```

Before running, set `TORN_API_KEY` in your shell. On PowerShell:

```powershell
$env:TORN_API_KEY='your-key'
pigeon-ingest --once
```

To run the Discord bot:

```bash
pigeon-bot
```

## Environment Variables

- `TORN_API_KEY`: Torn API key
- `TORN_LOG_ID`: log id to fetch, default `4103`
- `TORN_LIMIT`: number of logs to fetch per request, default `10`
- `TORN_POLL_INTERVAL_SECONDS`: polling interval, default `2`
- `TORN_DEPOSIT_ITEM_ID`: item to credit, default `206`
- `TORN_DEPOSIT_UNIT_VALUE`: value per item, default `800000`
- `TORN_DATABASE_PATH`: SQLite path, default `data/ingest.sqlite3`
- `TORN_BASE_URL`: default `https://api.torn.com/v2`
- `TORN_TIMEOUT_SECONDS`: request timeout, default `15`
- `DISCORD_TOKEN`: Discord bot token
- `DISCORD_GUILD_ID`: if set, slash commands sync to this guild immediately
- `DISCORD_ADMIN_USER_IDS`: comma-separated list of admin user IDs

## Bot Commands

- `/balance [user_id]`
- `/ledger [limit]`
- `/deposit <source_log_id> <user_id> <qty> [item_id] [note]`
- `/adjust <user_id> <delta> [reason]`
- `/rollback <ledger_id> [reason]`
- `/withdraw <amount> [note]`
- `/raffle [note]`

Notes:

- `/deposit` is admin-only and is meant for manual recovery when ingest was down.
- `/raffle` only works when the user balance is at least `800000`; the bot deducts exactly `800000` on entry.
