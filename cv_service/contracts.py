from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .settings import parse_line


class ProcessorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["sub", "main"] = "sub"
    line: str | None = None
    direction: Literal["any", "positive", "negative"] = "any"

    @field_validator("line")
    @classmethod
    def validate_line(cls, value: str | None) -> str | None:
        if value is not None:
            parse_line(value)
        return value


@dataclass(frozen=True)
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float
    label: str

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)
