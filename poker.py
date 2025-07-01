"""Simple heads-up poker helper for Discord bot."""

from __future__ import annotations

import random
from typing import List

from treys import Deck, Evaluator, Card
import discord



def card_to_emoji(card: int) -> str:
    """Return a simple text representation for a card."""
    rank = Card.get_rank_int(card)
    suit = Card.get_suit_int(card)
    ranks = "23456789TJQKA"
    suits = "cdhs"  # clubs, diamonds, hearts, spades
    return f"{ranks[rank-2]}{suits[suit]}"


class PokerGame:
    """Represents a minimal heads-up hold'em game."""

    def __init__(self) -> None:
        self.deck = Deck()
        self.player_hand = self.deck.draw(2)
        self.bot_hand = self.deck.draw(2)
        self.board: List[int] = []
        self.evaluator = Evaluator()
        self.stage = 0  # 0: pre,1:flop,2:turn,3:river,4:done

    def advance(self) -> bool:
        """Reveal next street. Returns True if game continues."""
        if self.stage == 0:
            self.board.extend(self.deck.draw(3))
            self.stage = 1
        elif self.stage == 1:
            self.board.extend(self.deck.draw(1))
            self.stage = 2
        elif self.stage == 2:
            self.board.extend(self.deck.draw(1))
            self.stage = 3
        else:
            self.stage = 4
        return self.stage < 4

    def result(self) -> str:
        """Return winner text."""
        ps = self.evaluator.evaluate(self.board, self.player_hand)
        bs = self.evaluator.evaluate(self.board, self.bot_hand)
        if ps < bs:
            return "You win!"
        if bs < ps:
            return "Bot wins!"
        return "It's a tie!"

    def format_board(self) -> str:
        return " ".join(card_to_emoji(c) for c in self.board)

    def format_hand(self, cards: List[int]) -> str:
        return " ".join(card_to_emoji(c) for c in cards)


class PokerView(discord.ui.View):
    def __init__(self, game: PokerGame, author: discord.User):
        super().__init__(timeout=120)
        self.game = game
        self.author = author

    async def _update(self, interaction: discord.Interaction):
        content = f"Board: {self.game.format_board()}"
        if self.game.stage >= 3:
            content += f"\n{self.game.result()}"
            self.stop()
            for child in self.children:
                child.disabled = True
        await interaction.response.edit_message(content=content, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button):
        if interaction.user != self.author:
            await interaction.response.send_message("You are not playing!", ephemeral=True)
            return
        self.game.advance()
        await self._update(interaction)

