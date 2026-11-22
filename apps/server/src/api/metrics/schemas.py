"""Usage over a period."""

from datetime import date, datetime

from pydantic import AwareDatetime, Field

from src.api.common import Input, Result, Schema
from src.db import models as db

# --- DATA MODEL --- #


class UsageDay(Schema):
    """This object represents the usage of one UTC day."""

    day: date = Field(description="UTC day")
    turns: int = Field(description="Turns started that day")
    input_tokens: int = Field(description="Input tokens")
    output_tokens: int = Field(description="Output tokens")
    cache_read_tokens: int = Field(description="Cache-read tokens")
    cost_usd: float = Field(description="Spending in USD")


class MetricsSummary(Schema):
    """This object represents the time, tokens and cost of turns and runs started in a period."""

    start: datetime = Field(description="Start of the period, inclusive")
    end: datetime = Field(description="End of the period, exclusive")
    chats: int = Field(description="Chats created in the period")
    turns: dict[db.TurnStatus, int] = Field(description="Turns by status")
    turn_seconds: float = Field(description="Total duration of finished turns")
    input_tokens: int = Field(description="Input tokens")
    output_tokens: int = Field(description="Output tokens")
    cache_read_tokens: int = Field(description="Cache-read tokens")
    cost_usd: float = Field(description="Spending in USD")
    executions: dict[db.ExecutionStatus, int] = Field(description="Code runs by status")
    execution_seconds: float = Field(description="Total duration of finished runs")
    days: list[UsageDay] = Field(description="Daily series of turns, tokens and cost")


# --- QUERY PARAMS --- #


class ReadMetricsSummaryInput(Input):
    """This object represents the input used to read the metrics summary."""

    start: AwareDatetime | None = Field(
        default=None, description="_Optional_. Start of the period; 30 days before `end` by default"
    )
    end: AwareDatetime | None = Field(
        default=None, description="_Optional_. End of the period; now by default"
    )


# --- RESULTS --- #


class ReadMetricsSummaryResult(Result[MetricsSummary]):
    """This object represents the result when the metrics summary is read."""
