# How to Deploy on a VM

This guide shows how to deploy the project on a Linux VM and run it like a real backend.
It works for Google Cloud, Oracle Cloud, and other small VPS providers.

Recommended setup:

- `pigeon-ingest` runs continuously as a `systemd` service
- `pigeon-bot` runs as a separate `systemd` service
- SQLite stays on the VM locally, so no public port is required

## Runtime Model

The application is intentionally split into two processes:

- `pigeon-ingest` pulls Torn logs and writes to SQLite
- `pigeon-bot` connects to Discord and serves slash commands

That means:

- locally, you usually run two terminals if you want both processes up at once
- on a VM, you should run them as two separate services, not as one combined process

## 1. Create the VM

Create a small Linux VM. For Google Cloud Free Tier, an `e2-micro` instance in an eligible US region is enough for this project.
For Oracle Cloud, a small Always Free instance is also enough when capacity is available.

- Image: Debian 12 or Ubuntu 22.04 LTS
- Shape: a small VM is enough for light load
- Networking: only SSH `22` inbound from your IP address
- No HTTP/HTTPS port is needed if you only run ingest + Discord bot

SSH into the machine once the instance is ready.

## 2. Install system packages

On the VM, install Python and basic tools:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip sqlite3
```

If you use Oracle Linux, replace that with `dnf` or `yum` equivalents as needed.

## 3. Clone the repo

```bash
git clone https://github.com/idontloveyou727/Pigeon.git
cd Pigeon
```

## 4. Create a virtualenv and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

## 5. Create the `.env` file

The project automatically reads `.env` from the current directory or a parent directory.
Create a `.env` file in the repo root with content like this:

```dotenv
TORN_API_KEY=your-torn-api-key
TORN_LOG_ID=4103
TORN_LIMIT=10
TORN_POLL_INTERVAL_SECONDS=30
TORN_DEPOSIT_ITEM_ID=206
TORN_DEPOSIT_UNIT_VALUE=800000
TORN_DATABASE_PATH=data/ingest.sqlite3
TORN_BASE_URL=https://api.torn.com/v2
TORN_TIMEOUT_SECONDS=15

DISCORD_TOKEN=your-discord-bot-token
DISCORD_GUILD_ID=your-guild-id
DISCORD_ADMIN_USER_IDS=123456789012345678
```

Notes:

- Do not commit `.env` to git
- If the bot is only used in one server, set `DISCORD_GUILD_ID` so slash commands sync faster
- `DISCORD_ADMIN_USER_IDS` accepts a comma-separated list of IDs if you have multiple admins
- `TORN_POLL_INTERVAL_SECONDS=30` is a safer default for a small 24/7 service. Lower values call the Torn API more often.

## 6. Run a manual smoke test

Run one ingest cycle first to verify Torn connectivity and SQLite writes:

```bash
source .venv/bin/activate
pigeon-ingest --once
```

If the log shows `Fetched ... Torn logs` and `Stored Torn log ...`, the ingest path is working.

Run the bot once:

```bash
pigeon-bot
```

When the bot comes online, it syncs slash commands to the configured guild.

## 7. Create the `pigeon-ingest` service

Create `/etc/systemd/system/pigeon-ingest.service`:

```ini
[Unit]
Description=Pigeon Torn Ingest Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_VM_USER
WorkingDirectory=/home/YOUR_VM_USER/Pigeon
EnvironmentFile=/home/YOUR_VM_USER/Pigeon/.env
ExecStart=/home/YOUR_VM_USER/Pigeon/.venv/bin/pigeon-ingest
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Replace `YOUR_VM_USER` with your Linux username, for example `phucnguyenquang727` on Google Cloud or `ubuntu` on many Ubuntu images.

If the browser SSH terminal has trouble with copy/paste, create the file with `sudo nano` or paste this single block:

```bash
sudo tee /etc/systemd/system/pigeon-ingest.service > /dev/null <<'EOF'
[Unit]
Description=Pigeon Torn Ingest Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_VM_USER
WorkingDirectory=/home/YOUR_VM_USER/Pigeon
EnvironmentFile=/home/YOUR_VM_USER/Pigeon/.env
ExecStart=/home/YOUR_VM_USER/Pigeon/.venv/bin/pigeon-ingest
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pigeon-ingest
sudo systemctl status pigeon-ingest
```

## 8. Create the `pigeon-bot` service

Create `/etc/systemd/system/pigeon-bot.service`:

```ini
[Unit]
Description=Pigeon Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_VM_USER
WorkingDirectory=/home/YOUR_VM_USER/Pigeon
EnvironmentFile=/home/YOUR_VM_USER/Pigeon/.env
ExecStart=/home/YOUR_VM_USER/Pigeon/.venv/bin/pigeon-bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Replace `YOUR_VM_USER` with your Linux username.

If copy/paste into an editor is unreliable, paste this single block:

```bash
sudo tee /etc/systemd/system/pigeon-bot.service > /dev/null <<'EOF'
[Unit]
Description=Pigeon Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_VM_USER
WorkingDirectory=/home/YOUR_VM_USER/Pigeon
EnvironmentFile=/home/YOUR_VM_USER/Pigeon/.env
ExecStart=/home/YOUR_VM_USER/Pigeon/.venv/bin/pigeon-bot
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pigeon-bot
sudo systemctl status pigeon-bot
```

## 9. View logs

View ingest logs in real time:

```bash
journalctl -u pigeon-ingest -f
```

View bot logs in real time:

```bash
journalctl -u pigeon-bot -f
```

## 10. Update the code

When a new commit is pushed to GitHub:

```bash
cd /home/YOUR_VM_USER/Pigeon
git pull
source .venv/bin/activate
python -m pip install -e .
sudo systemctl restart pigeon-ingest
sudo systemctl restart pigeon-bot
```

## 11. Important notes

- SQLite lives in `data/ingest.sqlite3`, so it persists on the VM across restarts
- If you want backups, copy that SQLite file
- The Discord bot does not need a public port because it only makes outbound connections to Discord
- If you want to scale later, you can move SQLite to PostgreSQL and keep the same service layout

## 12. How to inspect the database

For admin work, the database is the SQLite file at `data/ingest.sqlite3` on the VM.
The normal workflow is:

1. SSH into the VM
2. Open the SQLite file with the `sqlite3` CLI
3. Run read-only queries as needed

You can inspect it directly on the VM with the SQLite CLI:

```bash
sqlite3 data/ingest.sqlite3
```

If you want to query it without opening an interactive shell, you can run a one-off command like this:

```bash
sqlite3 data/ingest.sqlite3 "SELECT * FROM user_balances ORDER BY updated_at DESC LIMIT 20;"
```

Useful queries:

```sql
.tables
SELECT * FROM user_balances ORDER BY updated_at DESC LIMIT 20;
SELECT * FROM balance_ledger ORDER BY id DESC LIMIT 20;
SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 20;
SELECT * FROM raw_logs ORDER BY received_at DESC LIMIT 20;
```

If `sqlite3` is not installed, add it with your system package manager:

```bash
sudo apt install -y sqlite3
```

If you want to inspect the database locally on your own machine, copy the file from the VM first:

```bash
scp YOUR_VM_USER@your-vm:/home/YOUR_VM_USER/Pigeon/data/ingest.sqlite3 ./ingest.sqlite3
```

Then open the copied file locally with the same `sqlite3` commands.

## 13. Quick health checks

Check whether both services are running:

```bash
sudo systemctl is-active pigeon-ingest
sudo systemctl is-active pigeon-bot
```

Check recent ingest runs:

```bash
sqlite3 data/ingest.sqlite3 "SELECT id, status, fetched_count, inserted_count, duplicate_count, deposit_count, deposit_amount, error_message FROM ingest_runs ORDER BY id DESC LIMIT 10;"
```

If `inserted_count=0` and `duplicate_count` is positive, the ingest service is running but the newest Torn logs were already stored.

## 14. Fastest deployment path

If you only want to get it running quickly on a VM, do this in order:

1. Clone the repo
2. Create `.venv`
3. Create `.env`
4. Run `pigeon-ingest --once`
5. Run `pigeon-bot`
6. Once both work, convert those two commands into `systemd` services
