"""Validation for the UCI Steel energy CSV contract."""

import numpy as np
import pandas as pd


STEEL_COLUMNS = [
    "date", "Usage_kWh", "Lagging_Current_Reactive.Power_kVarh",
    "Leading_Current_Reactive_Power_kVarh", "CO2(tCO2)",
    "Lagging_Current_Power_Factor", "Leading_Current_Power_Factor", "NSM",
    "WeekStatus", "Day_of_week", "Load_Type",
]


def validate_steel_frame(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return parsed timestamps and target, rejecting ambiguous training input."""
    if frame.empty:
        raise ValueError("Steel dataset is empty.")
    missing = [column for column in STEEL_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"Steel dataset is missing required columns: {', '.join(missing)}")
    if frame[STEEL_COLUMNS].isna().any().any():
        raise ValueError("Steel dataset contains missing values in original fields.")
    timestamps = pd.to_datetime(frame["date"], format="%d/%m/%Y %H:%M", errors="coerce")
    if timestamps.isna().any():
        raise ValueError("Invalid date: expected dd/mm/YYYY HH:MM.")
    if timestamps.duplicated().any():
        raise ValueError("Duplicate timestamps are not allowed.")
    target = pd.to_numeric(frame["Usage_kWh"], errors="coerce")
    if not np.isfinite(target.to_numpy(dtype=float)).all() or (target < 0).any():
        raise ValueError("Usage_kWh must contain finite, non-negative numbers.")
    expected_week_status = np.where(timestamps.dt.dayofweek >= 5, "Weekend", "Weekday")
    if not (frame["WeekStatus"].to_numpy() == expected_week_status).all():
        raise ValueError("WeekStatus conflicts with the parsed calendar date.")
    if not (frame["Day_of_week"].to_numpy() == timestamps.dt.day_name().to_numpy()).all():
        raise ValueError("Day_of_week conflicts with the parsed calendar date.")
    return timestamps, target.astype(float)
