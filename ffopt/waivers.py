"""Optional priority analysis for an explicitly assumed, compressed MDP.

There are no fitted or default success rates here. A user supplies every
probability and reward. Rank is the entire state; opportunity draws, success
probabilities and transition matrices are stationary. Rewards are additive
utilities in consistent user-chosen units, not championship probabilities.

Each period reveals an opportunity, then the user spends or waits. A spend
earns ``success_probabilities[rank - 1] * reward`` in expectation. Its transition
row is UNCONDITIONAL on success (including failed claims); the user must model
any reset to the back of the queue. Waiting uses its own transition row. The
terminal value is zero. Exactness applies only to this finite assumed MDP.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping, Sequence
from numbers import Real


LIMITATION = (
    "Exact only within the compressed, user-supplied finite-horizon MDP; "
    "not a calibrated football model. Probabilities, opportunity rewards and "
    "rank transitions are assumptions. No season, title-odds or claim-success "
    "guarantee is implied."
)


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _probability(value: object, label: str) -> float:
    number = _finite(value, label)
    if not 0 <= number <= 1:
        raise ValueError(f"{label} must be in [0, 1]")
    return number


def _sequence(value: object, label: str) -> tuple:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be an array")
    return tuple(value)


def _normalized(values: tuple[float, ...], label: str) -> None:
    if not values or not math.isclose(math.fsum(values), 1.0, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"{label} probabilities must sum to 1")


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be nonempty text")
    return value


def _keys(raw: Mapping, required: set[str], optional: set[str], label: str) -> None:
    missing, unknown = required - raw.keys(), raw.keys() - required - optional
    if missing or unknown:
        raise ValueError(
            f"{label}: missing fields {sorted(missing)}; "
            f"unknown fields {sorted(map(str, unknown))}"
        )


@dataclasses.dataclass(frozen=True, slots=True)
class Opportunity:
    probability: float
    reward: float
    available: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "probability", _probability(self.probability, "opportunity"))
        object.__setattr__(self, "reward", _finite(self.reward, "opportunity reward"))
        if not isinstance(self.available, bool):
            raise ValueError("opportunity available must be a boolean")
        if not self.available and self.reward != 0:
            raise ValueError("an unavailable opportunity must have reward 0")


@dataclasses.dataclass(frozen=True, slots=True)
class WaiverModel:
    horizon: int
    opportunities: tuple[Opportunity, ...]
    success_probabilities: tuple[float, ...]
    wait_transitions: tuple[tuple[float, ...], ...]
    spend_transitions: tuple[tuple[float, ...], ...]
    label: str = "User-supplied scenario"
    assumptions: tuple[str, ...] = ()
    uncertainty: str = "Input probabilities and rewards have not been calibrated."

    def __post_init__(self) -> None:
        _integer(self.horizon, "horizon")
        _text(self.label, "label")
        _text(self.uncertainty, "uncertainty")
        assumptions = _sequence(self.assumptions, "assumptions")
        for assumption in assumptions:
            _text(assumption, "assumption")
        object.__setattr__(self, "assumptions", assumptions)
        opportunities = _sequence(self.opportunities, "opportunities")
        if any(not isinstance(o, Opportunity) for o in opportunities):
            raise ValueError("opportunities must contain Opportunity instances")
        _normalized(tuple(o.probability for o in opportunities), "opportunity")
        object.__setattr__(self, "opportunities", opportunities)
        success = tuple(
            _probability(p, f"success probability for rank {i + 1}")
            for i, p in enumerate(_sequence(self.success_probabilities, "success_probabilities"))
        )
        if not success:
            raise ValueError("at least one rank is required")
        object.__setattr__(self, "success_probabilities", success)
        for name in ("wait_transitions", "spend_transitions"):
            rows = _sequence(getattr(self, name), name)
            if len(rows) != len(success):
                raise ValueError(f"{name} must have one row per rank")
            matrix = []
            for i, row in enumerate(rows):
                probabilities = tuple(
                    _probability(p, f"{name} rank {i + 1}")
                    for p in _sequence(row, f"{name} rank {i + 1}")
                )
                if len(probabilities) != len(success):
                    raise ValueError(f"{name} must have one column per rank")
                _normalized(probabilities, f"{name} rank {i + 1}")
                matrix.append(probabilities)
            object.__setattr__(self, name, tuple(matrix))

    @property
    def ranks(self) -> int:
        return len(self.success_probabilities)

    @classmethod
    def from_dict(cls, raw: Mapping) -> WaiverModel:
        """Validate a JSON-compatible model without inventing missing rates.

        Required fields: horizon (periods INCLUDING the current decision),
        opportunities ([{probability, reward, available?: bool}]),
        success_probabilities ([probability per rank]), wait_transitions and
        spend_transitions (square row-stochastic matrices, rank 1 first).
        Optional fields: label, assumptions ([text]), uncertainty (text).

        An ``available: false, reward: 0`` event explicitly models no opportunity
        and forces waiting. Opportunity probabilities must sum to one, including
        such events. Transition rows must sum to one; success probabilities are
        independent Bernoulli parameters and need not sum to one.
        """
        if not isinstance(raw, Mapping):
            raise ValueError("waiver model must be an object")
        _keys(
            raw,
            {"horizon", "opportunities", "success_probabilities",
             "wait_transitions", "spend_transitions"},
            {"label", "assumptions", "uncertainty"},
            "waiver model",
        )
        opportunities = []
        for item in _sequence(raw["opportunities"], "opportunities"):
            if not isinstance(item, Mapping):
                raise ValueError("each opportunity must be an object")
            _keys(item, {"probability", "reward"}, {"available"}, "opportunity")
            opportunities.append(Opportunity(**item))
        return cls(**{**raw, "opportunities": tuple(opportunities)})


def _expectation(probabilities: tuple[float, ...], values: tuple[float, ...]) -> float:
    try:
        value = math.fsum(p * v for p, v in zip(probabilities, values))
    except OverflowError as exc:
        raise ValueError("model values exceed finite numeric range") from exc
    return _finite(value, "model value")


@dataclasses.dataclass(frozen=True, slots=True)
class WaiverDecision:
    rank: int
    periods_remaining: int
    reward: float
    spend_value: float | None
    wait_value: float
    threshold: float | None
    action: str
    reason: str
    model_label: str
    assumptions: tuple[str, ...]
    uncertainty: str
    limitation: str = LIMITATION


@dataclasses.dataclass(frozen=True, slots=True)
class WaiverSolution:
    model: WaiverModel
    values: tuple[tuple[float, ...], ...]

    def decision(
        self, *, rank: int, reward: float, remaining: int | None = None,
    ) -> WaiverDecision:
        """Condition on an observed opportunity; rank is one-based.

        The threshold is the successful-claim reward needed to equal waiting,
        not an automatic valuation of any actual player. Ties preserve priority.
        No finite reward threshold exists at zero success probability.
        """
        _integer(rank, "rank", 1)
        if rank > self.model.ranks:
            raise ValueError(f"rank must be in 1..{self.model.ranks}")
        reward = _finite(reward, "current reward")
        remaining = self.model.horizon if remaining is None else _integer(
            remaining, "remaining"
        )
        if remaining > self.model.horizon:
            raise ValueError("remaining exceeds the model horizon")
        wait, spend, threshold = 0.0, None, None
        action, reason = "wait", "Horizon exhausted; no further claims are valued."
        if remaining:
            i, previous = rank - 1, self.values[remaining - 1]
            wait = _expectation(self.model.wait_transitions[i], previous)
            continuation = _expectation(self.model.spend_transitions[i], previous)
            probability = self.model.success_probabilities[i]
            spend = _finite(probability * reward + continuation, "spend value")
            if probability:
                threshold = _finite((wait - continuation) / probability, "threshold")
                reason = "Spend only above the reward threshold; ties wait."
            else:
                reason = (
                    "Zero success probability: no finite reward threshold; "
                    "only the supplied transition values distinguish actions."
                )
            action = "spend" if spend > wait else "wait"
        return WaiverDecision(
            rank, remaining, reward, spend, wait, threshold, action, reason,
            self.model.label, self.model.assumptions, self.model.uncertainty,
        )


def solve_waivers(model: WaiverModel) -> WaiverSolution:
    """Bellman DP; ``values[t][rank - 1]`` is before observing an opportunity."""
    if not isinstance(model, WaiverModel):
        raise ValueError("an explicitly supplied, validated WaiverModel is required")
    values = [(0.0,) * model.ranks]
    probabilities = tuple(o.probability for o in model.opportunities)
    for _ in range(model.horizon):
        previous, current = values[-1], []
        for i, success in enumerate(model.success_probabilities):
            wait = _expectation(model.wait_transitions[i], previous)
            continuation = _expectation(model.spend_transitions[i], previous)
            opportunity_values = tuple(
                max(wait, _finite(success * o.reward + continuation, "spend value"))
                if o.available else wait
                for o in model.opportunities
            )
            current.append(_expectation(probabilities, opportunity_values))
        values.append(tuple(current))
    return WaiverSolution(model, tuple(values))


def evaluate_waiver(model: WaiverModel, *, rank: int, reward: float) -> WaiverDecision:
    """Convenience API for one observed opportunity; never synthesizes a model."""
    return solve_waivers(model).decision(rank=rank, reward=reward)
