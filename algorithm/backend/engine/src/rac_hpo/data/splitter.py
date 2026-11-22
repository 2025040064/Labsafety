"""Deterministic chronological date-block splitting with calendar-day gaps."""

import pandas as pd


def split_calendar_days(groups: pd.Series, gap_days: int = 1) -> tuple[dict, dict]:
    """Split retained days approximately 60/20/20, dropping each boundary gap."""
    if gap_days < 1:
        raise ValueError("gap_days must be at least one full calendar day.")
    dates = sorted(groups.unique())
    usable_days = len(dates) - 2 * gap_days
    train_count = int(usable_days * 0.6)
    validation_count = int(usable_days * 0.2)
    test_count = usable_days - train_count - validation_count
    if min(train_count, validation_count, test_count) < 1:
        raise ValueError("Dataset needs at least 7 distinct dates for 60/20/20 splitting and gaps.")
    first_boundary = train_count
    second_start = first_boundary + gap_days
    second_boundary = second_start + validation_count
    date_splits = {
        "train": dates[:first_boundary],
        "validation": dates[second_start:second_boundary],
        "test": dates[second_boundary + gap_days:],
        "gap": dates[first_boundary:second_start] + dates[second_boundary:second_boundary + gap_days],
    }
    splits = {
        name: groups.index[groups.isin(values)].tolist()
        for name, values in date_splits.items()
    }
    for earlier, later in (("train", "validation"), ("validation", "test")):
        elapsed = (pd.Timestamp(date_splits[later][0]) - pd.Timestamp(date_splits[earlier][-1])).days
        if elapsed < gap_days + 1:
            raise ValueError("Chronological splitting did not retain the required calendar-day gap.")
    return splits, date_splits
