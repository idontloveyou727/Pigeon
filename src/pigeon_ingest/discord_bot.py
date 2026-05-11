from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from .bot_config import BotConfig
from .ledger import BalanceChangeResult
from .storage import IngestStorage


class PigeonDiscordBot(commands.Bot):
    def __init__(self, config: BotConfig, storage: IngestStorage) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self._config = config
        self._storage = storage
        self._synced = False

    async def setup_hook(self) -> None:
        await self._storage.open()
        await self._sync_commands()

    async def close(self) -> None:
        await super().close()
        await self._storage.close()

    async def _sync_commands(self) -> None:
        if self._synced:
            return

        logger = logging.getLogger(__name__)
        if self._config.guild_id is not None:
            guild = discord.Object(id=self._config.guild_id)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to guild %s", self._config.guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global slash commands")

        self._synced = True


def build_bot(config: BotConfig) -> PigeonDiscordBot:
    storage = IngestStorage(config.database_path)
    bot = PigeonDiscordBot(config, storage)
    logger = logging.getLogger(__name__)

    async def _is_admin(interaction: discord.Interaction) -> bool:
        if not config.admin_user_ids:
            return True
        if interaction.user is None:
            return False
        return interaction.user.id in config.admin_user_ids

    async def _send(interaction: discord.Interaction, content: str, *, ephemeral: bool = False) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral)
            return
        await interaction.response.send_message(content, ephemeral=ephemeral)

    def _format_balance(user_id: int, balance: int, updated_at: str) -> str:
        return f"user_id={user_id} balance={balance} updated_at={updated_at}"

    @bot.event
    async def on_ready() -> None:
        logger.info("Discord bot ready as %s", bot.user)

    @bot.tree.command(name="balance", description="Check a user's balance")
    @app_commands.describe(user_id="Optional target user id")
    async def balance_command(interaction: discord.Interaction, user_id: int | None = None) -> None:
        target_user_id = user_id or interaction.user.id
        snapshot = await storage.get_balance(target_user_id)
        await _send(interaction, _format_balance(snapshot.user_id, snapshot.balance, snapshot.updated_at), ephemeral=True)

    @bot.tree.command(name="ledger", description="Show recent ledger entries")
    @app_commands.describe(limit="Number of recent entries to show")
    async def ledger_command(interaction: discord.Interaction, limit: int = 10) -> None:
        entries = await storage.list_recent_ledger(max(1, min(limit, 25)))
        if not entries:
            await _send(interaction, "Ledger is empty.", ephemeral=True)
            return

        lines = [
            f"#{entry.id} {entry.entry_type} user={entry.user_id} delta={entry.delta} key={entry.entry_key}"
            for entry in entries
        ]
        await _send(interaction, "\n".join(lines), ephemeral=True)

    @bot.tree.command(name="deposit", description="Emergency deposit for server downtime only")
    @app_commands.describe(
        source_log_id="Torn log id used as unique reference",
        user_id="Recipient user id",
        qty="Quantity of item 206 or configured deposit item",
        item_id="Item id, default 206",
        note="Optional audit note",
    )
    async def deposit_command(
        interaction: discord.Interaction,
        source_log_id: str,
        user_id: int,
        qty: int,
        item_id: int = 206,
        note: str | None = None,
    ) -> None:
        if not await _is_admin(interaction):
            await _send(interaction, "You are not allowed to use this command.", ephemeral=True)
            return

        result = await storage.record_deposit(
            source_log_id=source_log_id,
            user_id=user_id,
            qty=qty,
            item_id=item_id,
            unit_value=800000,
            actor=str(interaction.user.id),
            reason=note or "Emergency manual deposit",
        )
        if result is None:
            await _send(interaction, "Deposit already exists, no change applied.", ephemeral=True)
            return
        await _send(interaction, _format_change_result("Deposited", result), ephemeral=True)

    @bot.tree.command(name="adjust", description="Manual balance adjustment for admins")
    @app_commands.describe(user_id="Target user id", delta="Positive or negative amount", reason="Optional note")
    async def adjust_command(
        interaction: discord.Interaction,
        user_id: int,
        delta: int,
        reason: str | None = None,
    ) -> None:
        if not await _is_admin(interaction):
            await _send(interaction, "You are not allowed to use this command.", ephemeral=True)
            return

        result = await storage.adjust_balance(user_id=user_id, delta=delta, actor=str(interaction.user.id), reason=reason)
        await _send(interaction, _format_change_result("Adjusted", result), ephemeral=True)

    @bot.tree.command(name="rollback", description="Rollback a ledger entry")
    @app_commands.describe(entry_id="Ledger entry id to rollback", reason="Optional note")
    async def rollback_command(interaction: discord.Interaction, entry_id: int, reason: str | None = None) -> None:
        if not await _is_admin(interaction):
            await _send(interaction, "You are not allowed to use this command.", ephemeral=True)
            return

        result = await storage.rollback_ledger_entry(entry_id=entry_id, actor=str(interaction.user.id), reason=reason)
        if result is None:
            await _send(interaction, "Ledger entry not found or already rolled back.", ephemeral=True)
            return

        await _send(interaction, _format_change_result("Rolled back", result), ephemeral=True)

    @bot.tree.command(name="withdraw", description="Placeholder withdraw command for admins")
    @app_commands.describe(amount="Withdraw amount", note="Optional note")
    async def withdraw_command(interaction: discord.Interaction, amount: int, note: str | None = None) -> None:
        if not await _is_admin(interaction):
            await _send(interaction, "You are not allowed to use this command.", ephemeral=True)
            return

        await _send(
            interaction,
            f"Withdraw placeholder received: amount={amount} note={note or 'placeholder'}. Hook the real action later.",
            ephemeral=True,
        )

    @bot.tree.command(name="raffle", description="Join raffle if balance is at least 800,000")
    @app_commands.describe(note="Optional note")
    async def raffle_command(interaction: discord.Interaction, note: str | None = None) -> None:
        target_user_id = interaction.user.id
        snapshot = await storage.get_balance(target_user_id)
        if snapshot.balance < 800000:
            await _send(
                interaction,
                f"Insufficient balance. Need 800000, current balance={snapshot.balance}.",
                ephemeral=True,
            )
            return

        result = await storage.adjust_balance(
            user_id=target_user_id,
            delta=-800000,
            actor=str(target_user_id),
            reason=note or "Raffle participation",
            entry_type="raffle_entry",
        )
        await _send(
            interaction,
            (
                f"Raffle joined: user_id={result.ledger_entry.user_id} amount=800000 "
                f"before={result.before_balance} after={result.after_balance} ledger_id={result.ledger_entry.id}"
            ),
            ephemeral=True,
        )

    return bot


def _format_change_result(label: str, result: BalanceChangeResult) -> str:
    return (
        f"{label}: user_id={result.ledger_entry.user_id} delta={result.ledger_entry.delta} "
        f"before={result.before_balance} after={result.after_balance} ledger_id={result.ledger_entry.id}"
    )


async def _async_main() -> None:
    config = BotConfig.from_env()
    bot = build_bot(config)
    try:
        await bot.start(config.token)
    finally:
        await bot.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()