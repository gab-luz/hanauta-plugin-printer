#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PrinterState(str, Enum):
    IDLE = "idle"
    PRINTING = "printing"
    PAUSED = "paused"
    STOPPED = "stopped"
    ERROR = "error"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class SupplyInfo:
    name: str
    level_percent: int | None = None
    state: str = "unknown"
    message: str = ""
    raw_level: int | None = None


@dataclass(slots=True)
class PrintJob:
    job_id: int
    printer_name: str
    title: str
    user: str
    state: str
    state_text: str
    created_at_epoch: int
    age_seconds: int
    size_kb: int
    progress_percent: int | None = None


@dataclass(slots=True)
class AlertItem:
    level: AlertLevel
    code: str
    message: str
    detail: str = ""
    source: str = ""
    timestamp_epoch: int = 0


@dataclass(slots=True)
class PrinterInfo:
    name: str
    state: PrinterState
    state_text: str
    is_default: bool = False
    queue_count: int = 0
    needs_attention: bool = False
    model: str = ""
    location: str = ""
    accepting_jobs: bool = True
    reasons_raw: list[str] = field(default_factory=list)
    reasons_text: list[str] = field(default_factory=list)
    supplies: list[SupplyInfo] = field(default_factory=list)


@dataclass(slots=True)
class PrinterSnapshot:
    ok: bool
    backend_available: bool
    cups_available: bool
    default_printer: str
    printers: list[PrinterInfo] = field(default_factory=list)
    active_jobs: list[PrintJob] = field(default_factory=list)
    recent_jobs: list[PrintJob] = field(default_factory=list)
    alerts: list[AlertItem] = field(default_factory=list)
    diagnosis: str = ""
    diagnosis_detail: str = ""
    refreshed_at_epoch: int = 0
    raw_error: str = ""


def to_plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [to_plain(item) for item in value]
    if hasattr(value, "__dataclass_fields__"):
        payload: dict[str, Any] = {}
        for key in value.__dataclass_fields__.keys():
            payload[key] = to_plain(getattr(value, key))
        return payload
    return value
