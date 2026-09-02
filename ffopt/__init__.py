"""Draft optimization toolkit for the 'Deeply Unserious League'.

Framing: a sequential, exclusive resource-allocation problem. Items (players) have a
type (position) and an uncertain scalar payoff. Only items placed in a constrained
lineup score; surplus items score zero. The objective is to maximize expected
constrained-lineup payoff, not raw payoff.
"""

__all__ = ["config", "client", "scoring", "pool", "valuation"]
