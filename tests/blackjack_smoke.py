from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pigeon_ingest.blackjack import BlackjackGame, BlackjackHand, Shoe, apply_action, hand_value, settle_game


def test_soft_17_stands() -> None:
    assert hand_value(["AS", "6H"]) == (17, True)


def test_natural_blackjack_pays_three_to_two() -> None:
    game = BlackjackGame(
        table_id=1,
        user_id=123,
        discord_user_id=456,
        hands=[BlackjackHand(cards=["AS", "KH"], bet=10_000)],
        dealer_cards=["9S", "7H"],
    )
    shoe = Shoe(cards=["2S"])
    results = settle_game(game, shoe)
    assert results[0].payout == 25_000


def test_split_aces_get_one_card_each_and_are_done() -> None:
    game = BlackjackGame(
        table_id=1,
        user_id=123,
        discord_user_id=456,
        hands=[BlackjackHand(cards=["AS", "AH"], bet=10_000)],
        dealer_cards=["9S", "7H"],
    )
    shoe = Shoe(cards=["5S", "6D"])
    apply_action(game, "split", shoe)
    assert len(game.hands) == 2
    assert all(hand.split_aces and hand.stood and len(hand.cards) == 2 for hand in game.hands)


def test_split_does_not_allow_resplit() -> None:
    game = BlackjackGame(
        table_id=1,
        user_id=123,
        discord_user_id=456,
        hands=[BlackjackHand(cards=["9S", "9H"], bet=10_000)],
        dealer_cards=["9C", "7H"],
    )
    shoe = Shoe(cards=["9D", "2D"])
    apply_action(game, "split", shoe)
    assert not game.hands[0].can_split()
    assert not game.hands[1].can_split()


if __name__ == "__main__":
    test_soft_17_stands()
    test_natural_blackjack_pays_three_to_two()
    test_split_aces_get_one_card_each_and_are_done()
    test_split_does_not_allow_resplit()
    print("blackjack-smoke-ok")
