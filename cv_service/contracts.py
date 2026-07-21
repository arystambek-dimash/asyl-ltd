from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from .settings import parse_camera, parse_line


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


class AlwaysOnOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cameras: list[str]
    source: Literal["sub", "main"] = "sub"


class RecordingDeleteOptions(BaseModel):
    """Exact MediaMTX segments approved for deletion by the CRM."""
    model_config = ConfigDict(extra="forbid")

    stream: str
    starts: list[str]

    @field_validator("stream")
    @classmethod
    def validate_stream(cls, value: str) -> str:
        # Session recordings are published as cam<N>ai only.
        stream = value.strip()
        if not stream.endswith("ai"):
            raise ValueError("recording stream must end with ai")
        parse_camera(stream[:-2])
        return stream

    @field_validator("starts")
    @classmethod
    def validate_starts(cls, values: list[str]) -> list[str]:
        if len(values) > 1000:
            raise ValueError("too many recording segments")
        cleaned = [str(value).strip() for value in values]
        if any(not value or len(value) > 64 for value in cleaned):
            raise ValueError("invalid recording segment start")
        return list(dict.fromkeys(cleaned))


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
