from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import random
from typing import Literal


RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
SUITS = ("S", "H", "D", "C")
MIN_BET = 10_000
MAX_BET = 10_000_000
SHOE_DECKS = 4
RESHUFFLE_USED_RATIO = 0.70
RESHUFFLE_AFTER = timedelta(hours=1)

Action = Literal["hit", "stand", "double", "split"]


@dataclass
class Shoe:
    cards: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    initial_size: int = SHOE_DECKS * len(RANKS) * len(SUITS)

    @classmethod
    def fresh(cls) -> "Shoe":
        cards = [f"{rank}{suit}" for _ in range(SHOE_DECKS) for rank in RANKS for suit in SUITS]
        random.shuffle(cards)
        return cls(cards=cards)

    def should_reshuffle(self) -> bool:
        if not self.cards:
            return True
        used = self.initial_size - len(self.cards)
        if used / self.initial_size >= RESHUFFLE_USED_RATIO:
            return True
        return datetime.now(timezone.utc) - self.created_at >= RESHUFFLE_AFTER

    def draw(self) -> str:
        if self.should_reshuffle():
            replacement = Shoe.fresh()
            self.cards = replacement.cards
            self.created_at = replacement.created_at
            self.initial_size = replacement.initial_size
        return self.cards.pop()


@dataclass
class BlackjackHand:
    cards: list[str]
    bet: int
    from_split: bool = False
    split_aces: bool = False
    stood: bool = False
    doubled: bool = False

    @property
    def value(self) -> int:
        return hand_value(self.cards)[0]

    @property
    def soft(self) -> bool:
        return hand_value(self.cards)[1]

    @property
    def busted(self) -> bool:
        return self.value > 21

    @property
    def natural_blackjack(self) -> bool:
        return not self.from_split and is_natural_blackjack(self.cards)

    def can_double(self) -> bool:
        return len(self.cards) == 2 and not self.stood and not self.split_aces

    def can_split(self) -> bool:
        if self.from_split or self.split_aces or len(self.cards) != 2:
            return False
        return card_rank(self.cards[0]) == card_rank(self.cards[1])


@dataclass
class BlackjackGame:
    table_id: int
    user_id: int
    discord_user_id: int
    hands: list[BlackjackHand]
    dealer_cards: list[str]
    active_hand_index: int = 0
    settled: bool = False

    @property
    def active_hand(self) -> BlackjackHand:
        return self.hands[self.active_hand_index]

    def all_player_hands_done(self) -> bool:
        return all(hand.stood or hand.busted or hand.split_aces for hand in self.hands)


@dataclass(frozen=True)
class HandResult:
    label: str
    hand: BlackjackHand
    dealer_value: int
    payout: int


def start_game(*, table_id: int, user_id: int, discord_user_id: int, bet: int, shoe: Shoe) -> BlackjackGame:
    player = BlackjackHand(cards=[shoe.draw(), shoe.draw()], bet=bet)
    dealer_cards = [shoe.draw(), shoe.draw()]
    return BlackjackGame(
        table_id=table_id,
        user_id=user_id,
        discord_user_id=discord_user_id,
        hands=[player],
        dealer_cards=dealer_cards,
    )


def apply_action(game: BlackjackGame, action: Action, shoe: Shoe) -> None:
    hand = game.active_hand
    if hand.stood or hand.busted:
        advance_hand(game)
        return

    if action == "hit":
        hand.cards.append(shoe.draw())
        if hand.busted:
            hand.stood = True
            advance_hand(game)
        return

    if action == "stand":
        hand.stood = True
        advance_hand(game)
        return

    if action == "double":
        if not hand.can_double():
            raise ValueError("Double down is not available")
        hand.bet *= 2
        hand.doubled = True
        hand.cards.append(shoe.draw())
        hand.stood = True
        advance_hand(game)
        return

    if action == "split":
        if not hand.can_split():
            raise ValueError("Split is not available")
        first, second = hand.cards
        split_aces = card_rank(first) == "A"
        game.hands[game.active_hand_index] = BlackjackHand(
            cards=[first, shoe.draw()],
            bet=hand.bet,
            from_split=True,
            split_aces=split_aces,
            stood=split_aces,
        )
        game.hands.insert(
            game.active_hand_index + 1,
            BlackjackHand(
                cards=[second, shoe.draw()],
                bet=hand.bet,
                from_split=True,
                split_aces=split_aces,
                stood=split_aces,
            ),
        )
        if split_aces:
            advance_hand(game)
        return


def advance_hand(game: BlackjackGame) -> None:
    while game.active_hand_index < len(game.hands) - 1:
        game.active_hand_index += 1
        hand = game.active_hand
        if not hand.stood and not hand.busted and not hand.split_aces:
            return


def settle_game(game: BlackjackGame, shoe: Shoe) -> list[HandResult]:
    while dealer_should_hit(game.dealer_cards):
        game.dealer_cards.append(shoe.draw())

    dealer_value = hand_value(game.dealer_cards)[0]
    dealer_blackjack = is_natural_blackjack(game.dealer_cards)
    dealer_bust = dealer_value > 21
    results: list[HandResult] = []

    for index, hand in enumerate(game.hands, start=1):
        if hand.busted:
            results.append(HandResult(label=f"Hand {index}: lose", hand=hand, dealer_value=dealer_value, payout=0))
            continue
        if hand.natural_blackjack and not dealer_blackjack:
            payout = hand.bet + hand.bet * 3 // 2
            results.append(
                HandResult(label=f"Hand {index}: natural blackjack", hand=hand, dealer_value=dealer_value, payout=payout)
            )
            continue
        if dealer_blackjack and not hand.natural_blackjack:
            results.append(HandResult(label=f"Hand {index}: lose", hand=hand, dealer_value=dealer_value, payout=0))
            continue
        if dealer_bust or hand.value > dealer_value:
            results.append(HandResult(label=f"Hand {index}: win", hand=hand, dealer_value=dealer_value, payout=hand.bet * 2))
            continue
        if hand.value == dealer_value:
            results.append(HandResult(label=f"Hand {index}: push", hand=hand, dealer_value=dealer_value, payout=hand.bet))
            continue
        results.append(HandResult(label=f"Hand {index}: lose", hand=hand, dealer_value=dealer_value, payout=0))

    game.settled = True
    return results


def dealer_should_hit(cards: list[str]) -> bool:
    value, _soft = hand_value(cards)
    return value < 17


def hand_value(cards: list[str]) -> tuple[int, bool]:
    total = 0
    aces = 0
    for card in cards:
        rank = card_rank(card)
        if rank == "A":
            aces += 1
            total += 11
        elif rank in {"J", "Q", "K"}:
            total += 10
        else:
            total += int(rank)

    soft = aces > 0
    while total > 21 and aces:
        total -= 10
        aces -= 1
    soft = soft and aces > 0
    return total, soft


def is_natural_blackjack(cards: list[str]) -> bool:
    if len(cards) != 2:
        return False
    ranks = {card_rank(card) for card in cards}
    return "A" in ranks and bool(ranks & {"10", "J", "Q", "K"})


def card_rank(card: str) -> str:
    return card[:-1]


def format_cards(cards: list[str]) -> str:
    return " ".join(cards)
