from __future__ import annotations

import asyncio
import logging
import re

import discord
from discord import app_commands
from discord.ext import commands

from .blackjack import MAX_BET, MIN_BET, is_natural_blackjack
from .blackjack_manager import BlackjackManager, format_game, format_results
from .bot_config import BotConfig
from .ledger import BalanceChangeResult
from .storage import IngestStorage


class PigeonDiscordBot(commands.Bot):
    def __init__(self, config: BotConfig, storage: IngestStorage, blackjack: BlackjackManager) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self._config = config
        self._storage = storage
        self._blackjack = blackjack
        self._synced = False

    async def setup_hook(self) -> None:
        await self._storage.open()
        recovered = await self._blackjack.recover_or_refund_sessions()
        logger = logging.getLogger(__name__)
        for message in recovered:
            logger.warning("Recovered blackjack session: %s", message)
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
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Synced slash commands to guild %s", self._config.guild_id)
        else:
            await self.tree.sync()
            logger.info("Synced global slash commands")

        self._synced = True


def build_bot(config: BotConfig) -> PigeonDiscordBot:
    storage = IngestStorage(config.database_path)
    blackjack = BlackjackManager(storage)
    bot = PigeonDiscordBot(config, storage, blackjack)
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

    async def _game_user_id(interaction: discord.Interaction) -> int | None:
        user = interaction.user
        names = [
            getattr(user, "display_name", ""),
            getattr(user, "global_name", ""),
            getattr(user, "name", ""),
        ]
        for name in names:
            match = re.search(r"\[(\d+)\]", name or "")
            if match:
                return int(match.group(1))
        await _send(
            interaction,
            "Could not find your game user id in your server nickname. Expected a name like Player [123456].",
            ephemeral=True,
        )
        return None

    @bot.event
    async def on_ready() -> None:
        logger.info("Discord bot ready as %s", bot.user)

    @bot.tree.command(name="balance", description="Check a user's balance")
    @app_commands.describe(user_id="Optional target user id")
    async def balance_command(interaction: discord.Interaction, user_id: int | None = None) -> None:
        target_user_id = user_id or await _game_user_id(interaction)
        if target_user_id is None:
            return
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

    @bot.tree.command(name="withdraw", description="Request a manual in-game withdrawal")
    @app_commands.describe(amount="Withdraw amount", note="Optional note")
    async def withdraw_command(interaction: discord.Interaction, amount: int, note: str | None = None) -> None:
        target_user_id = await _game_user_id(interaction)
        if target_user_id is None:
            return

        if amount <= 0:
            await _send(interaction, "Withdraw amount must be positive.", ephemeral=True)
            return

        snapshot = await storage.get_balance(target_user_id)
        if snapshot.balance < amount:
            await _send(
                interaction,
                f"Insufficient balance. Requested={amount}, current balance={snapshot.balance}.",
                ephemeral=True,
            )
            return

        result = await storage.adjust_balance(
            user_id=target_user_id,
            delta=-amount,
            actor=str(interaction.user.id),
            reason=note or "Manual in-game withdrawal request",
            entry_type="withdraw_request",
        )

        admin_message = (
            f"Withdraw request: {interaction.user.mention} user_id={target_user_id} needs {amount}. "
            f"Balance before={result.before_balance} after={result.after_balance}. "
            f"Ledger id={result.ledger_entry.id}. Please complete it manually in game."
        )
        delivered = 0
        for admin_user_id in config.admin_user_ids:
            try:
                admin_user = await bot.fetch_user(admin_user_id)
                await admin_user.send(admin_message)
                delivered += 1
            except discord.HTTPException:
                logger.exception("Failed to DM withdraw request to admin user_id=%s", admin_user_id)

        if delivered == 0:
            await storage.rollback_ledger_entry(
                entry_id=result.ledger_entry.id,
                actor="withdraw-dm-failure",
                reason="Withdraw request could not be delivered to any admin DM",
            )
            await _send(
                interaction,
                "Withdraw request failed because the bot could not DM any configured admin. Your balance was refunded.",
                ephemeral=True,
            )
            return

        await _send(
            interaction,
            (
                f"Withdraw request sent. Amount={amount}, balance before={result.before_balance}, "
                f"after={result.after_balance}."
            ),
            ephemeral=True,
        )

    @bot.tree.command(name="raffle", description="Join raffle if balance is at least 800,000")
    @app_commands.describe(note="Optional note")
    async def raffle_command(interaction: discord.Interaction, note: str | None = None) -> None:
        target_user_id = await _game_user_id(interaction)
        if target_user_id is None:
            return
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

    bj_group = app_commands.Group(name="bj", description="Play blackjack with your balance")

    @bj_group.command(name="start", description="Start a blackjack table")
    @app_commands.describe(amount=f"Bet amount, min {MIN_BET}, max {MAX_BET}")
    async def blackjack_start(interaction: discord.Interaction, amount: int) -> None:
        user_id = await _game_user_id(interaction)
        if user_id is None:
            return
        try:
            game = await blackjack.start(user_id=user_id, discord_user_id=interaction.user.id, bet=amount)
        except ValueError as exc:
            await _send(interaction, str(exc), ephemeral=True)
            return

        if game.active_hand.natural_blackjack or is_natural_blackjack(game.dealer_cards):
            settled = await blackjack.auto_stand(table_id=game.table_id)
            if settled is None:
                await _send(interaction, "Blackjack table finished before it could be shown.", ephemeral=True)
                return
            settled_game, results = settled
            await _send(interaction, format_results(settled_game, results), ephemeral=True)
            return

        view = BlackjackView(blackjack, game.table_id)
        await interaction.response.send_message(format_game(game), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @bj_group.command(name="exit", description="Exit your current blackjack table")
    async def blackjack_exit(interaction: discord.Interaction) -> None:
        refund = await blackjack.exit(discord_user_id=interaction.user.id)
        if refund is None:
            await _send(interaction, "You do not have an active blackjack table.", ephemeral=True)
            return
        await _send(interaction, f"Exited blackjack table. Refunded {refund}.", ephemeral=True)

    bot.tree.add_command(bj_group)

    return bot


class BlackjackView(discord.ui.View):
    def __init__(self, manager: BlackjackManager, table_id: int) -> None:
        super().__init__(timeout=30)
        self._manager = manager
        self._table_id = table_id
        self.message: discord.Message | None = None
        game = manager.get(table_id)
        if game is not None:
            hand = game.active_hand
            for child in self.children:
                if isinstance(child, discord.ui.Button) and child.label == "Double":
                    child.disabled = not hand.can_double()
                if isinstance(child, discord.ui.Button) and child.label == "Split":
                    child.disabled = not hand.can_split()

    async def on_timeout(self) -> None:
        settled = await self._manager.auto_stand(table_id=self._table_id)
        if settled is None or self.message is None:
            return
        game, results = settled
        try:
            await self.message.edit(content=format_results(game, results) + "\nTimed out: auto-stand applied.", view=None)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, "hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, "stand")

    @discord.ui.button(label="Double", style=discord.ButtonStyle.success)
    async def double(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, "double")

    @discord.ui.button(label="Split", style=discord.ButtonStyle.success)
    async def split(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._apply(interaction, "split")

    @discord.ui.button(label="Exit", style=discord.ButtonStyle.danger)
    async def exit(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        refund = await self._manager.exit(discord_user_id=interaction.user.id)
        self.stop()
        if refund is None:
            await interaction.response.edit_message(content="This blackjack table is already closed.", view=None)
            return
        await interaction.response.edit_message(content=f"Exited blackjack table. Refunded {refund}.", view=None)

    async def _apply(self, interaction: discord.Interaction, action: str) -> None:
        try:
            game, results = await self._manager.action(
                table_id=self._table_id,
                discord_user_id=interaction.user.id,
                action=action,  # type: ignore[arg-type]
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.stop()
        if results is not None:
            await interaction.response.edit_message(content=format_results(game, results), view=None)
            return

        view = BlackjackView(self._manager, game.table_id)
        await interaction.response.edit_message(content=format_game(game), view=view)
        view.message = await interaction.original_response()


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
