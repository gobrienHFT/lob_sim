"""Deterministic, auditable helpers for market-making research studies."""

from .protocol import (
    BootstrapInterval,
    ResearchRegistry,
    UTCDaySplit,
    chronological_day_split,
    moving_block_bootstrap_mean,
    paired_moving_block_bootstrap_mean_delta,
)

__all__ = [
    "BootstrapInterval",
    "ResearchRegistry",
    "UTCDaySplit",
    "chronological_day_split",
    "moving_block_bootstrap_mean",
    "paired_moving_block_bootstrap_mean_delta",
]
