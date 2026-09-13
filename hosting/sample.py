"""The only database contents approved for this unauthenticated checkpoint."""

from typing import Mapping, Sequence

DATASET = "hosting-probe-v1"
SAMPLE_ROWS = (
    {"id": 1, "label": "synthetic-alpha", "value": 10},
    {"id": 2, "label": "synthetic-beta", "value": 20},
    {"id": 3, "label": "synthetic-gamma", "value": 30},
)


class UnexpectedSample(ValueError):
    pass


def public_sample(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    valid = len(rows) == len(SAMPLE_ROWS) and all(
        set(row) == {"id", "label", "value"}
        and type(row["id"]) is int
        and type(row["value"]) is int
        and type(row["label"]) is str
        and row == expected
        for row, expected in zip(rows, SAMPLE_ROWS, strict=True)
    )
    if not valid:
        raise UnexpectedSample("Database contents differ from the approved synthetic sample.")
    return {"dataset": DATASET, "rows": [dict(row) for row in rows]}
