
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)

import discord
from treys import Card, Deck, Evaluator


# Basic text emoji for card suits
SUIT_MAP = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
RANKS = "23456789TJQKA"

def card_to_emoji(card: int) -> str:
    rank = Card.get_rank_int(card)
    suit_char = Card.INT_SUIT_TO_CHAR_SUIT[Card.get_suit_int(card)]
    return f"{RANKS[rank]}{SUIT_MAP[suit_char]}"


def format_hand(cards: List[int]) -> str:
    return " ".join(card_to_emoji(c) for c in cards)


@dataclass
class Player:
    user: discord.abc.User
    chips: int = 50000
    hand: List[int] | None = None
    bet: int = 0
    acted: bool = False
    folded: bool = False


class PokerMatch:
    small_blind = 500
    big_blind = 1000

    def __init__(self, p1: discord.abc.User, p2: discord.abc.User, bot_user: discord.abc.User):
        self.players = [Player(p1), Player(p2)]
        self.bot_user = bot_user
        self.evaluator = Evaluator()
        self.dealer = 0
        self.deck = Deck()
        self.board: List[int] = []
        self.pot = 0
        self.current_bet = 0
        self.turn = 0
        self.stage = ""
        self.message: Optional[discord.Message] = None

    async def start(self, channel: discord.abc.Messageable):
        self.channel = channel
        await self._start_hand()

    async def _start_hand(self):
        self.deck = Deck()
        self.board = []
        for p in self.players:
            p.hand = self.deck.draw(2)
            p.bet = 0
            p.acted = False
            p.folded = False
        self.pot = 0
        self.current_bet = 0
        self.stage = "preflop"
        self.dealer ^= 1  # alternate dealer
        sb = self.dealer
        bb = 1 - self.dealer
        await self._send_hands()
        self._post_blind(sb, self.small_blind)
        self._post_blind(bb, self.big_blind)
        self.turn = sb
        await self._update_message(initial=True)
        if self._current_player_is_bot():
            await self._bot_action()

    def _post_blind(self, idx: int, amount: int):
        p = self.players[idx]
        blind = min(amount, p.chips)
        p.chips -= blind
        p.bet = blind
        self.pot += blind
        self.current_bet = max(self.current_bet, blind)

    async def _send_hands(self):
        for p in self.players:
            try:
                dm = await p.user.create_dm()
                await dm.send(f"Your hand: {format_hand(p.hand)}")
            except discord.Forbidden as e:
                logger.warning("Failed to send hand to %s: %s", p.user, e)
                await self.channel.send(
                    f"{p.user.mention} さん、DM がオフになっているためハンドを送れませんでした。"
                )
            except discord.HTTPException as e:
                logger.error("HTTP error when sending hand to %s: %s", p.user, e)
                await self.channel.send(
                    f"{p.user.mention} さんへのDM送信でエラーが発生しました。"
                )

    def _current_player_is_bot(self) -> bool:
        return self.players[self.turn].user.id == self.bot_user.id

    def _next_turn(self):
        self.turn ^= 1

    async def player_action(self, user: discord.abc.User, action: str, raise_to: int | None = None):
        if user.id != self.players[self.turn].user.id:
            return
        p = self.players[self.turn]
        opp = self.players[self.turn ^ 1]

        if action == "fold":
            p.folded = True
            await self._finish_hand(winner=opp)
            return

        to_call = self.current_bet - p.bet
        if action == "call":
            amount = min(to_call, p.chips)
            p.chips -= amount
            p.bet += amount
            self.pot += amount
            p.acted = True
        elif action == "check":
            p.acted = True
        elif action == "raise":
            if raise_to is None:
                raise_to = self.current_bet + self.big_blind
            amount = min(raise_to - p.bet, p.chips)
            p.chips -= amount
            p.bet += amount
            self.pot += amount
            self.current_bet = p.bet
            p.acted = True
            opp.acted = False
            if amount == p.chips and p.chips == 0:
                await self._send_effect(f"{p.user.display_name} ALL-IN! 💥")
        elif action == "allin":
            await self.player_action(user, "raise", p.bet + p.chips)
            return

        if p.chips == 0 and not p.folded:
            p.acted = True
        if opp.folded:
            await self._finish_hand(winner=p)
            return
        if p.acted and opp.acted and p.bet == opp.bet:
            await self._next_stage()
        else:
            self._next_turn()
        await self._update_message()
        if self._current_player_is_bot():
            await self._bot_action()

    async def _next_stage(self):
        for pl in self.players:
            pl.bet = 0
            pl.acted = False
        self.current_bet = 0
        if self.stage == "preflop":
            self.stage = "flop"
            self.board.extend(self.deck.draw(3))
            self.turn = 1 - self.dealer
        elif self.stage == "flop":
            self.stage = "turn"
            self.board.extend(self.deck.draw(1))
            self.turn = 1 - self.dealer
        elif self.stage == "turn":
            self.stage = "river"
            self.board.extend(self.deck.draw(1))
            self.turn = 1 - self.dealer
        elif self.stage == "river":
            await self._showdown()
            return

    async def _showdown(self):
        p0, p1 = self.players
        s0 = self.evaluator.evaluate(self.board, p0.hand)
        s1 = self.evaluator.evaluate(self.board, p1.hand)
        if s0 < s1:
            await self._finish_hand(winner=p0)
        elif s1 < s0:
            await self._finish_hand(winner=p1)
        else:
            p0.chips += self.pot // 2
            p1.chips += self.pot - self.pot // 2
            await self.channel.send("It's a tie!")
            await self._check_game_end()

    async def _finish_hand(self, winner: Player):
        winner.chips += self.pot
        await self.channel.send(f"{winner.user.display_name} wins {self.pot} 💰")
        await self._check_game_end()

    async def _check_game_end(self):
        losers = [p.user.display_name for p in self.players if p.chips <= 0]
        if losers:
            names = ", ".join(losers)
            await self.channel.send(f"Game over! {names} ran out of chips.")
            return
        await self._start_hand()

    async def _send_effect(self, text: str):
        await self.channel.send(text)

    async def _bot_action(self):
        await asyncio.sleep(1)
        p = self.players[self.turn]
        to_call = self.current_bet - p.bet
        if to_call > 0:
            if random.random() < 0.3:
                action = "fold"
            else:
                action = "call"
        else:
            if random.random() < 0.3:
                action = "raise"
            else:
                action = "check"
        if action == "raise":
            raise_to = self.current_bet + self.big_blind
            await self.player_action(p.user, action, raise_to)
        else:
            await self.player_action(p.user, action)

    async def _update_message(self, initial: bool = False):
        desc = f"Pot: 💰{self.pot}\n"
        desc += f"Board: {format_hand(self.board)}\n"
        desc += "\n".join(
            f"{pl.user.display_name}: 💰{pl.chips}  Bet {pl.bet}" for pl in self.players
        )
        if not initial:
            desc += f"\nWaiting for {self.players[self.turn].user.display_name}"
        embed = discord.Embed(description=desc)
        if self.message is None:
            self.message = await self.channel.send(embed=embed)
        else:
            try:
                await self.message.edit(embed=embed)
            except discord.HTTPException:
                self.message = await self.channel.send(embed=embed)


class PokerView(discord.ui.View):
    def __init__(self, game: PokerMatch):
        super().__init__(timeout=None)
        self.game = game

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return True

    async def _act(self, interaction: discord.Interaction, action: str, raise_to: int | None = None):
        await interaction.response.defer()
        await self.game.player_action(interaction.user, action, raise_to)

    @discord.ui.button(label="Fold", style=discord.ButtonStyle.danger)
    async def fold(self, interaction: discord.Interaction, _):
        await self._act(interaction, "fold")

    @discord.ui.button(label="Check/Call", style=discord.ButtonStyle.secondary)
    async def call(self, interaction: discord.Interaction, _):
        await self._act(interaction, "call" if self.game.current_bet > self.game.players[self.game.turn].bet else "check")

    @discord.ui.button(label="Raise +1BB", style=discord.ButtonStyle.primary)
    async def raise_small(self, interaction: discord.Interaction, _):
        amount = self.game.big_blind
        await self._act(interaction, "raise", self.game.current_bet + amount)

    @discord.ui.button(label="Raise Pot", style=discord.ButtonStyle.primary)
    async def raise_big(self, interaction: discord.Interaction, _):
        amount = self.game.pot if self.game.pot else self.game.big_blind * 5
        await self._act(interaction, "raise", self.game.current_bet + amount)

    @discord.ui.button(label="All-in", style=discord.ButtonStyle.success)
    async def allin(self, interaction: discord.Interaction, _):
        await self._act(interaction, "allin")

