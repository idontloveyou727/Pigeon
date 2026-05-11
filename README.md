# Pigeon150m Ingest Service

Service ingest dữ liệu từ Torn API, lưu raw log vào SQLite và chống cộng trùng theo `log.id`.
Balance update cho item `206` được ghi vào ledger và cộng vào số dư user trong cùng database.

## Chạy

```bash
python -m pip install -e .
pigeon-ingest --once
```

Trước khi chạy, cần set `TORN_API_KEY` trong shell. Ví dụ trên PowerShell:

```powershell
$env:TORN_API_KEY='your-key'
pigeon-ingest --once
```

Bot Discord interactive chạy bằng slash commands, không dùng webhook:

```bash
pigeon-bot
```

## Biến môi trường

- `TORN_API_KEY`: API key Torn
- `TORN_LOG_ID`: log id cần pull, mặc định `4103`
- `TORN_LIMIT`: số log mỗi lần fetch, mặc định `10`
- `TORN_POLL_INTERVAL_SECONDS`: chu kỳ poll, mặc định `2`
- `TORN_DEPOSIT_ITEM_ID`: item cần cộng tiền, mặc định `206`
- `TORN_DEPOSIT_UNIT_VALUE`: giá trị mỗi item, mặc định `800000`
- `TORN_DATABASE_PATH`: đường dẫn SQLite, mặc định `data/ingest.sqlite3`
- `TORN_BASE_URL`: mặc định `https://api.torn.com/v2`
- `TORN_TIMEOUT_SECONDS`: timeout request, mặc định `15`
- `DISCORD_TOKEN`: token bot Discord
- `DISCORD_GUILD_ID`: nếu có, slash commands sẽ sync vào guild này để hiện ngay
- `DISCORD_ADMIN_USER_IDS`: danh sách user id admin, phân tách bằng dấu phẩy

## Bot commands

- `/balance [user_id]`
- `/ledger [limit]`
- `/deposit <source_log_id> <user_id> <qty> [item_id] [note]`
- `/adjust <user_id> <delta> [reason]`
- `/rollback <ledger_id> [reason]`
- `/withdraw <amount> [note]`
- `/raffle [note]`

Ghi chú:

- `/deposit` chỉ dành cho admin và dùng cho trường hợp server sập, để cộng tay từ log chưa được ingest tự động.
- `/raffle` chỉ chạy khi balance hiện tại của người dùng >= `800000`; bot sẽ trừ đúng `800000` khi tham gia.
