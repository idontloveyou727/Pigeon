from __future__ import annotations

import asyncio
from dataclasses import asdict

from .blackjack import (
    MAX_BET,
    MIN_BET,
    Action,
    BlackjackGame,
    BlackjackHand,
    HandResult,
    Shoe,
    apply_action,
    settle_game,
    start_game,
)
from .storage import IngestStorage


class BlackjackManager:
    def __init__(self, storage: IngestStorage) -> None:
        self._storage = storage
        self._shoe = Shoe.fresh()
        self._games: dict[int, BlackjackGame] = {}
        self._lock = asyncio.Lock()

    async def recover_or_refund_sessions(self) -> list[str]:
        messages: list[str] = []
        for session in await self._storage.list_blackjack_sessions():
            state = session["state"]
            reserved = int(state.get("reserved", 0))
            if reserved > 0:
                await self._storage.adjust_balance(
                    user_id=int(session["user_id"]),
                    delta=reserved,
                    actor="blackjack-recovery",
                    reason=f"Refund active blackjack table {session['table_id']} after bot restart",
                    entry_type="blackjack_refund",
                )
            await self._storage.delete_blackjack_session(int(session["table_id"]))
            messages.append(f"refunded table={session['table_id']} user={session['user_id']} amount={reserved}")
        return messages

    async def start(self, *, user_id: int, discord_user_id: int, bet: int) -> BlackjackGame:
        if bet < MIN_BET or bet > MAX_BET:
            raise ValueError(f"Bet must be between {MIN_BET} and {MAX_BET}")

        async with self._lock:
            if any(game.user_id == user_id or game.discord_user_id == discord_user_id for game in self._games.values()):
                raise ValueError("You already have an active blackjack table")

            table_id = self._next_table_id()
            if table_id is None:
                raise ValueError("All blackjack tables are busy. Try again later.")

            balance = await self._storage.get_balance(user_id)
            if balance.balance < bet:
                raise ValueError(f"Insufficient balance. Need {bet}, current balance={balance.balance}.")

            await self._storage.adjust_balance(
                user_id=user_id,
                delta=-bet,
                actor=str(discord_user_id),
                reason=f"Blackjack table {table_id} initial bet",
                entry_type="blackjack_bet",
            )
            game = start_game(table_id=table_id, user_id=user_id, discord_user_id=discord_user_id, bet=bet, shoe=self._shoe)
            self._games[table_id] = game
            await self._save(game)
            return game

    async def exit(self, *, discord_user_id: int) -> int | None:
        async with self._lock:
            game = self._find_by_discord_user(discord_user_id)
            if game is None:
                return None
            refund = _reserved_amount(game)
            await self._storage.adjust_balance(
                user_id=game.user_id,
                delta=refund,
                actor=str(discord_user_id),
                reason=f"Blackjack table {game.table_id} exit refund",
                entry_type="blackjack_refund",
            )
            await self._finish(game)
            return refund

    async def action(self, *, table_id: int, discord_user_id: int, action: Action) -> tuple[BlackjackGame, list[HandResult] | None]:
        async with self._lock:
            game = self._games.get(table_id)
            if game is None:
                raise ValueError("This blackjack table is no longer active")
            if game.discord_user_id != discord_user_id:
                raise ValueError("This is not your blackjack table")

            if action == "double":
                if not game.active_hand.can_double():
                    raise ValueError("Double down is not available")
                extra_bet = game.active_hand.bet
                balance = await self._storage.get_balance(game.user_id)
                if balance.balance < extra_bet:
                    raise ValueError(f"Insufficient balance to double. Need {extra_bet}, current balance={balance.balance}.")
                await self._storage.adjust_balance(
                    user_id=game.user_id,
                    delta=-extra_bet,
                    actor=str(discord_user_id),
                    reason=f"Blackjack table {table_id} double down",
                    entry_type="blackjack_bet",
                )
            elif action == "split":
                if not game.active_hand.can_split():
                    raise ValueError("Split is not available")
                extra_bet = game.active_hand.bet
                balance = await self._storage.get_balance(game.user_id)
                if balance.balance < extra_bet:
                    raise ValueError(f"Insufficient balance to split. Need {extra_bet}, current balance={balance.balance}.")
                await self._storage.adjust_balance(
                    user_id=game.user_id,
                    delta=-extra_bet,
                    actor=str(discord_user_id),
                    reason=f"Blackjack table {table_id} split bet",
                    entry_type="blackjack_bet",
                )

            apply_action(game, action, self._shoe)
            if game.all_player_hands_done():
                results = await self._settle(game, actor=str(discord_user_id), reason="Blackjack table settled")
                return game, results

            await self._save(game)
            return game, None

    async def auto_stand(self, *, table_id: int) -> tuple[BlackjackGame, list[HandResult]] | None:
        async with self._lock:
            game = self._games.get(table_id)
            if game is None:
                return None
            while not game.all_player_hands_done():
                apply_action(game, "stand", self._shoe)
            results = await self._settle(game, actor="blackjack-timeout", reason="Blackjack table timeout auto-stand")
            return game, results

    def get(self, table_id: int) -> BlackjackGame | None:
        return self._games.get(table_id)

    async def _settle(self, game: BlackjackGame, *, actor: str, reason: str) -> list[HandResult]:
        results = settle_game(game, self._shoe)
        payout = sum(result.payout for result in results)
        if payout > 0:
            await self._storage.adjust_balance(
                user_id=game.user_id,
                delta=payout,
                actor=actor,
                reason=f"{reason}; table {game.table_id}",
                entry_type="blackjack_payout",
            )
        await self._finish(game)
        return results

    async def _finish(self, game: BlackjackGame) -> None:
        self._games.pop(game.table_id, None)
        await self._storage.delete_blackjack_session(game.table_id)

    async def _save(self, game: BlackjackGame) -> None:
        await self._storage.save_blackjack_session(
            table_id=game.table_id,
            user_id=game.user_id,
            discord_user_id=game.discord_user_id,
            state={
                "reserved": _reserved_amount(game),
                "game": _game_to_dict(game),
            },
        )

    def _next_table_id(self) -> int | None:
        for table_id in range(1, 4):
            if table_id not in self._games:
                return table_id
        return None

    def _find_by_discord_user(self, discord_user_id: int) -> BlackjackGame | None:
        for game in self._games.values():
            if game.discord_user_id == discord_user_id:
                return game
        return None


def format_game(game: BlackjackGame, *, reveal_dealer: bool = False) -> str:
    dealer_cards = format_cards(game.dealer_cards if reveal_dealer else game.dealer_cards[:1] + ["??"])
    lines = [
        f"Blackjack table {game.table_id}",
        f"User {game.user_id}",
        f"Dealer: {dealer_cards}",
    ]
    for index, hand in enumerate(game.hands, start=1):
        marker = ">" if index - 1 == game.active_hand_index and not game.all_player_hands_done() else "-"
        lines.append(
            f"{marker} Hand {index}: {format_cards(hand.cards)} value={hand.value} bet={hand.bet}"
        )
    return "\n".join(lines)


def format_results(game: BlackjackGame, results: list[HandResult]) -> str:
    lines = [format_game(game, reveal_dealer=True), "Results:"]
    for result in results:
        lines.append(f"{result.label}; payout={result.payout}")
    lines.append("")
    lines.append("/bj start amount:<bet> to continue playing, or /bj exit to leave the table.")
    return "\n".join(lines)


def _reserved_amount(game: BlackjackGame) -> int:
    return sum(hand.bet for hand in game.hands)


def format_cards(cards: list[str]) -> str:
    return " ".join(format_card(card) for card in cards)


def format_card(card: str) -> str:
    if card == "??":
        return "??"
    rank = card[:-1]
    suit = card[-1]
    icons = {
        "S": "♠",
        "H": "♥",
        "D": "♦",
        "C": "♣",
    }
    return f"{rank}{icons.get(suit, suit)}"


def _game_to_dict(game: BlackjackGame) -> dict:
    return {
        "table_id": game.table_id,
        "user_id": game.user_id,
        "discord_user_id": game.discord_user_id,
        "dealer_cards": game.dealer_cards,
        "active_hand_index": game.active_hand_index,
        "settled": game.settled,
        "hands": [asdict(hand) for hand in game.hands],
    }


def game_from_dict(payload: dict) -> BlackjackGame:
    return BlackjackGame(
        table_id=int(payload["table_id"]),
        user_id=int(payload["user_id"]),
        discord_user_id=int(payload["discord_user_id"]),
        dealer_cards=list(payload["dealer_cards"]),
        active_hand_index=int(payload["active_hand_index"]),
        settled=bool(payload["settled"]),
        hands=[
            BlackjackHand(
                cards=list(hand["cards"]),
                bet=int(hand["bet"]),
                from_split=bool(hand["from_split"]),
                split_aces=bool(hand["split_aces"]),
                stood=bool(hand["stood"]),
                doubled=bool(hand["doubled"]),
            )
            for hand in payload["hands"]
        ],
    )
