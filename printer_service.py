#!/usr/bin/env python3
from __future__ import annotations

import getpass
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

from printer_models import (
    AlertItem,
    AlertLevel,
    PrintJob,
    PrinterInfo,
    PrinterSnapshot,
    PrinterState,
    SupplyInfo,
    to_plain,
)

try:
    import cups  # type: ignore
except Exception:  # pragma: no cover
    cups = None

CACHE_DIR = Path.home() / ".local" / "state" / "hanauta" / "printer-widget"
CACHE_FILE = CACHE_DIR / "snapshot.json"


REASON_MAP: dict[str, tuple[AlertLevel, str]] = {
    "media-empty": (AlertLevel.WARNING, "Out of paper"),
    "media-needed": (AlertLevel.WARNING, "Paper needed"),
    "cover-open": (AlertLevel.WARNING, "Cover open"),
    "paused": (AlertLevel.WARNING, "Printer paused"),
    "offline": (AlertLevel.ERROR, "Offline or unreachable"),
    "connecting-to-device": (AlertLevel.WARNING, "Connecting to printer"),
    "printer-error": (AlertLevel.ERROR, "Printer error"),
    "marker-supply-low": (AlertLevel.WARNING, "Toner or ink low"),
    "marker-supply-empty": (AlertLevel.ERROR, "Toner or ink empty"),
    "toner-low": (AlertLevel.WARNING, "Toner low"),
    "toner-empty": (AlertLevel.ERROR, "Toner empty"),
    "fuser-error": (AlertLevel.ERROR, "Printer hardware error"),
    "door-open": (AlertLevel.WARNING, "Printer door open"),
    "authentication-required": (AlertLevel.ERROR, "Authentication required"),
    "cups-insecure-filter-warning": (AlertLevel.ERROR, "Filter/backend failed"),
    "com.apple.print.recoverable": (AlertLevel.WARNING, "Needs attention"),
    "none": (AlertLevel.INFO, "Ready"),
}

JOB_STATE_TEXT = {
    3: "Pending",
    4: "Held",
    5: "Printing",
    6: "Stopped",
    7: "Canceled",
    8: "Failed",
    9: "Completed",
}

ACTIVE_JOB_STATES = {3, 4, 5, 6}
RECENT_JOB_STATES = {7, 8, 9}


def _now() -> int:
    return int(time.time())


def _parse_reasons(raw: object) -> list[str]:
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw if str(item).strip()]
    else:
        text = str(raw or "").strip()
        values = [part.strip() for part in text.split(",") if part.strip()]
    return [item for item in values if item and item != "none"]


def _humanize_reason(reason: str) -> tuple[AlertLevel, str]:
    key = reason.strip().lower()
    if key in REASON_MAP:
        return REASON_MAP[key]
    pretty = re.sub(r"[-_]+", " ", key).strip().capitalize() or "Needs attention"
    return AlertLevel.WARNING, pretty


def _state_from_printer(printer_state: int, reasons: list[str], accepting_jobs: bool) -> PrinterState:
    lowered = {item.lower() for item in reasons}
    if "offline" in lowered:
        return PrinterState.OFFLINE
    if "paused" in lowered or not accepting_jobs:
        return PrinterState.PAUSED
    if printer_state == 3:
        return PrinterState.IDLE
    if printer_state == 4:
        return PrinterState.PRINTING
    if printer_state == 5:
        return PrinterState.STOPPED
    if "printer-error" in lowered or "authentication-required" in lowered:
        return PrinterState.ERROR
    return PrinterState.UNKNOWN


def _state_text(state: PrinterState) -> str:
    return {
        PrinterState.IDLE: "Idle",
        PrinterState.PRINTING: "Printing",
        PrinterState.PAUSED: "Paused",
        PrinterState.STOPPED: "Stopped",
        PrinterState.ERROR: "Error",
        PrinterState.OFFLINE: "Offline",
        PrinterState.UNKNOWN: "Unknown",
    }[state]


def _parse_supplies(attrs: dict[str, object]) -> list[SupplyInfo]:
    names = attrs.get("marker-names", [])
    levels = attrs.get("marker-levels", [])
    states = attrs.get("marker-types", [])
    messages = attrs.get("marker-message", [])
    if not isinstance(names, list):
        return []
    supplies: list[SupplyInfo] = []
    for index, name in enumerate(names):
        label = str(name or "").strip()
        if not label:
            continue
        level_raw = None
        if isinstance(levels, list) and index < len(levels):
            try:
                level_raw = int(levels[index])
            except Exception:
                level_raw = None
        level_percent = None
        if level_raw is not None and 0 <= level_raw <= 100:
            level_percent = level_raw
        state = ""
        if isinstance(states, list) and index < len(states):
            state = str(states[index] or "").strip().lower()
        message = ""
        if isinstance(messages, list) and index < len(messages):
            message = str(messages[index] or "").strip()
        supplies.append(
            SupplyInfo(
                name=label,
                level_percent=level_percent,
                state=state or "unknown",
                message=message,
                raw_level=level_raw,
            )
        )
    return supplies


def _job_from_attrs(job_id: int, attrs: dict[str, object], now_epoch: int) -> PrintJob:
    state_int = int(attrs.get("job-state", 3) or 3)
    created = int(attrs.get("time-at-creation", 0) or 0)
    if created <= 0:
        created = now_epoch
    title = str(attrs.get("job-name", "Untitled print job") or "Untitled print job").strip()
    user = str(attrs.get("job-originating-user-name", "") or "").strip()
    printer = str(attrs.get("job-printer-name", "") or "").strip()
    if not printer:
        uri = str(attrs.get("job-printer-uri", "") or "")
        printer = uri.rsplit("/", 1)[-1] if "/" in uri else ""
    size_kb = int(attrs.get("job-k-octets", 0) or 0)
    progress = attrs.get("job-media-progress")
    progress_percent = None
    if progress is not None:
        try:
            progress_percent = max(0, min(100, int(progress)))
        except Exception:
            progress_percent = None
    return PrintJob(
        job_id=int(job_id),
        printer_name=printer,
        title=title,
        user=user,
        state=str(state_int),
        state_text=JOB_STATE_TEXT.get(state_int, "Unknown"),
        created_at_epoch=created,
        age_seconds=max(0, now_epoch - created),
        size_kb=max(0, size_kb),
        progress_percent=progress_percent,
    )


def _diagnosis(snapshot: PrinterSnapshot) -> tuple[str, str]:
    if not snapshot.backend_available:
        return (
            "Printer backend unavailable",
            "Install python3-cups to enable full printer integration.",
        )
    if not snapshot.cups_available:
        return (
            "CUPS server unavailable",
            snapshot.raw_error or "Could not connect to CUPS/IPP.",
        )
    if not snapshot.printers:
        return (
            "No printers configured",
            "Add a printer in CUPS (http://localhost:631) or your distro settings.",
        )
    if not snapshot.default_printer:
        return ("No default printer configured", "Set one printer as default for quick actions.")
    paused = [p for p in snapshot.printers if p.state == PrinterState.PAUSED]
    if paused:
        return ("Queue paused", f"{paused[0].name} is paused. Resume to continue printing.")
    offline = [p for p in snapshot.printers if p.state in {PrinterState.OFFLINE, PrinterState.ERROR, PrinterState.STOPPED}]
    if offline:
        return (
            "Printer needs attention",
            f"{offline[0].name}: {', '.join(offline[0].reasons_text) or offline[0].state_text}",
        )
    if not snapshot.active_jobs:
        return ("No jobs in queue", "Queue is clear.")
    return ("Printing in progress", f"{len(snapshot.active_jobs)} active job(s).")


class PrinterService:
    def __init__(self) -> None:
        self._last_error = ""

    @property
    def backend_available(self) -> bool:
        return cups is not None

    def _connection(self):
        if cups is None:
            raise RuntimeError("python3-cups is not installed")
        try:
            return cups.Connection()
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(str(exc)) from exc

    def snapshot(self) -> PrinterSnapshot:
        now_epoch = _now()
        if cups is None:
            snap = PrinterSnapshot(
                ok=False,
                backend_available=False,
                cups_available=False,
                default_printer="",
                refreshed_at_epoch=now_epoch,
                raw_error="python3-cups import failed",
            )
            diag, detail = _diagnosis(snap)
            snap.diagnosis, snap.diagnosis_detail = diag, detail
            return snap

        try:
            conn = self._connection()
        except Exception as exc:
            snap = PrinterSnapshot(
                ok=False,
                backend_available=True,
                cups_available=False,
                default_printer="",
                refreshed_at_epoch=now_epoch,
                raw_error=str(exc),
            )
            diag, detail = _diagnosis(snap)
            snap.diagnosis, snap.diagnosis_detail = diag, detail
            return snap

        default_printer = ""
        try:
            default_printer = str(conn.getDefault() or "").strip()
        except Exception:
            default_printer = ""

        try:
            printers_raw = conn.getPrinters() or {}
        except Exception as exc:
            printers_raw = {}
            self._last_error = str(exc)

        try:
            jobs_raw = conn.getJobs(which_jobs="all") or {}
        except Exception:
            jobs_raw = {}

        jobs: list[PrintJob] = []
        for key, attrs in jobs_raw.items():
            if not isinstance(attrs, dict):
                continue
            try:
                job = _job_from_attrs(int(key), attrs, now_epoch)
            except Exception:
                continue
            jobs.append(job)

        active_jobs = [job for job in jobs if int(job.state) in ACTIVE_JOB_STATES]
        active_jobs.sort(key=lambda item: item.created_at_epoch)

        recent_jobs = [job for job in jobs if int(job.state) in RECENT_JOB_STATES]
        recent_jobs.sort(key=lambda item: item.created_at_epoch, reverse=True)
        recent_jobs = recent_jobs[:12]

        queue_count_by_printer: dict[str, int] = {}
        for job in active_jobs:
            queue_count_by_printer[job.printer_name] = queue_count_by_printer.get(job.printer_name, 0) + 1

        printers: list[PrinterInfo] = []
        alerts: list[AlertItem] = []
        for name, attrs in sorted(printers_raw.items(), key=lambda item: str(item[0]).lower()):
            if not isinstance(attrs, dict):
                continue
            printer_state = int(attrs.get("printer-state", 0) or 0)
            accepting_jobs = bool(attrs.get("printer-is-accepting-jobs", True))
            reasons_raw = _parse_reasons(attrs.get("printer-state-reasons", ""))
            reasons_text: list[str] = []
            needs_attention = False
            for reason in reasons_raw:
                level, message = _humanize_reason(reason)
                reasons_text.append(message)
                if level in {AlertLevel.WARNING, AlertLevel.ERROR}:
                    needs_attention = True
                    alerts.append(
                        AlertItem(
                            level=level,
                            code=reason,
                            message=message,
                            detail=reason,
                            source=str(name),
                            timestamp_epoch=now_epoch,
                        )
                    )
            state = _state_from_printer(printer_state, reasons_raw, accepting_jobs)
            if state in {PrinterState.ERROR, PrinterState.OFFLINE, PrinterState.PAUSED, PrinterState.STOPPED}:
                needs_attention = True
            info = PrinterInfo(
                name=str(name),
                state=state,
                state_text=_state_text(state),
                is_default=(str(name) == default_printer),
                queue_count=queue_count_by_printer.get(str(name), 0),
                needs_attention=needs_attention,
                model=str(attrs.get("printer-make-and-model", "") or "").strip(),
                location=str(attrs.get("printer-location", "") or "").strip(),
                accepting_jobs=accepting_jobs,
                reasons_raw=reasons_raw,
                reasons_text=reasons_text,
                supplies=_parse_supplies(attrs),
            )
            printers.append(info)

        if any(int(job.state) == 8 for job in recent_jobs):
            alerts.append(
                AlertItem(
                    level=AlertLevel.ERROR,
                    code="job-failed",
                    message="Recent print job failed",
                    detail="Check queue and printer reasons for details.",
                    source=default_printer,
                    timestamp_epoch=now_epoch,
                )
            )

        snap = PrinterSnapshot(
            ok=True,
            backend_available=True,
            cups_available=True,
            default_printer=default_printer,
            printers=printers,
            active_jobs=active_jobs,
            recent_jobs=recent_jobs,
            alerts=alerts[:25],
            refreshed_at_epoch=now_epoch,
        )
        diag, detail = _diagnosis(snap)
        snap.diagnosis, snap.diagnosis_detail = diag, detail
        return snap

    def pause_printer(self, printer_name: str) -> tuple[bool, str]:
        try:
            conn = self._connection()
            conn.disablePrinter(printer_name)
            return True, f"Paused {printer_name}."
        except Exception as exc:
            return False, f"Pause failed: {exc}"

    def resume_printer(self, printer_name: str) -> tuple[bool, str]:
        try:
            conn = self._connection()
            conn.enablePrinter(printer_name)
            conn.acceptJobs(printer_name)
            return True, f"Resumed {printer_name}."
        except Exception as exc:
            return False, f"Resume failed: {exc}"

    def cancel_job(self, job_id: int, printer_name: str = "") -> tuple[bool, str]:
        job_int = int(job_id)
        attempts: list[str] = []
        try:
            conn = self._connection()
        except Exception as exc:
            conn = None
            attempts.append(f"cups connect: {exc}")

        if conn is not None:
            # Some CUPS backends behave differently with purge_job; try both.
            try:
                conn.cancelJob(job_int)
                return True, f"Canceled job {job_int}."
            except Exception as exc:
                attempts.append(f"cancelJob: {exc}")
            try:
                conn.cancelJob(job_int, True)
                return True, f"Canceled job {job_int}."
            except Exception as exc:
                attempts.append(f"cancelJob(purge): {exc}")

        # Fallback to CUPS CLI, which can work in environments where pycups cancel is rejected.
        request_ids = [str(job_int)]
        if printer_name:
            request_ids.append(f"{printer_name}-{job_int}")
        for request_id in request_ids:
            cmd = ["cancel", request_id]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=4.0,
                    check=False,
                )
                if proc.returncode == 0:
                    return True, f"Canceled job {job_int}."
                detail = (proc.stderr or proc.stdout or "").strip()
                if detail:
                    attempts.append(f"{' '.join(cmd)}: {detail}")
                else:
                    attempts.append(f"{' '.join(cmd)} exited with {proc.returncode}")
            except Exception as exc:
                attempts.append(f"{' '.join(cmd)}: {exc}")

        detail = "; ".join(item for item in attempts if item) or "unknown error"
        return False, f"Cancel failed for job {job_int}: {detail}"

    def cancel_my_jobs(self, user: str | None = None, printer_name: str = "") -> tuple[bool, str]:
        target_user = (user or getpass.getuser() or "").strip()
        if not target_user:
            return False, "Unable to determine current user."
        try:
            conn = self._connection()
            jobs = conn.getJobs(which_jobs="not-completed") or {}
            canceled = 0
            failures: list[str] = []
            for job_id, attrs in jobs.items():
                if not isinstance(attrs, dict):
                    continue
                owner = str(attrs.get("job-originating-user-name", "") or "").strip()
                if owner != target_user:
                    continue
                j_printer = str(attrs.get("job-printer-name", "") or "").strip()
                if not j_printer:
                    uri = str(attrs.get("job-printer-uri", "") or "")
                    j_printer = uri.rsplit("/", 1)[-1] if "/" in uri else ""
                if printer_name and j_printer != printer_name:
                    continue
                ok, message = self.cancel_job(int(job_id), j_printer)
                if ok:
                    canceled += 1
                else:
                    failures.append(message)
            if failures and canceled == 0:
                return False, failures[0]
            if failures:
                return True, f"Canceled {canceled} job(s) for {target_user}. Some jobs failed."
            return True, f"Canceled {canceled} job(s) for {target_user}."
        except Exception as exc:
            return False, f"Cancel-all failed: {exc}"

    def set_default_printer(self, printer_name: str) -> tuple[bool, str]:
        try:
            conn = self._connection()
            conn.setDefault(printer_name)
            return True, f"{printer_name} set as default."
        except Exception as exc:
            return False, f"Set default failed: {exc}"


def save_snapshot_cache(snapshot: PrinterSnapshot) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(to_plain(snapshot), indent=2), encoding="utf-8")
    except Exception:
        pass


def load_snapshot_cache(max_age_seconds: int = 60) -> PrinterSnapshot | None:
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        ts = int(payload.get("refreshed_at_epoch", 0) or 0)
        if ts <= 0:
            return None
        if max_age_seconds > 0 and (_now() - ts) > max_age_seconds:
            return None
        printers = []
        for row in payload.get("printers", []):
            if not isinstance(row, dict):
                continue
            supplies = []
            for sup in row.get("supplies", []):
                if not isinstance(sup, dict):
                    continue
                supplies.append(
                    SupplyInfo(
                        name=str(sup.get("name", "") or ""),
                        level_percent=sup.get("level_percent"),
                        state=str(sup.get("state", "unknown") or "unknown"),
                        message=str(sup.get("message", "") or ""),
                        raw_level=sup.get("raw_level"),
                    )
                )
            try:
                state = PrinterState(str(row.get("state", "unknown")))
            except Exception:
                state = PrinterState.UNKNOWN
            printers.append(
                PrinterInfo(
                    name=str(row.get("name", "") or ""),
                    state=state,
                    state_text=str(row.get("state_text", "Unknown") or "Unknown"),
                    is_default=bool(row.get("is_default", False)),
                    queue_count=int(row.get("queue_count", 0) or 0),
                    needs_attention=bool(row.get("needs_attention", False)),
                    model=str(row.get("model", "") or ""),
                    location=str(row.get("location", "") or ""),
                    accepting_jobs=bool(row.get("accepting_jobs", True)),
                    reasons_raw=[str(item) for item in row.get("reasons_raw", []) if str(item).strip()],
                    reasons_text=[str(item) for item in row.get("reasons_text", []) if str(item).strip()],
                    supplies=supplies,
                )
            )
        jobs = []
        for row in payload.get("active_jobs", []):
            if not isinstance(row, dict):
                continue
            jobs.append(
                PrintJob(
                    job_id=int(row.get("job_id", 0) or 0),
                    printer_name=str(row.get("printer_name", "") or ""),
                    title=str(row.get("title", "") or ""),
                    user=str(row.get("user", "") or ""),
                    state=str(row.get("state", "") or ""),
                    state_text=str(row.get("state_text", "") or ""),
                    created_at_epoch=int(row.get("created_at_epoch", 0) or 0),
                    age_seconds=int(row.get("age_seconds", 0) or 0),
                    size_kb=int(row.get("size_kb", 0) or 0),
                    progress_percent=row.get("progress_percent"),
                )
            )
        alerts = []
        for row in payload.get("alerts", []):
            if not isinstance(row, dict):
                continue
            try:
                level = AlertLevel(str(row.get("level", "info")))
            except Exception:
                level = AlertLevel.INFO
            alerts.append(
                AlertItem(
                    level=level,
                    code=str(row.get("code", "") or ""),
                    message=str(row.get("message", "") or ""),
                    detail=str(row.get("detail", "") or ""),
                    source=str(row.get("source", "") or ""),
                    timestamp_epoch=int(row.get("timestamp_epoch", 0) or 0),
                )
            )
        return PrinterSnapshot(
            ok=bool(payload.get("ok", False)),
            backend_available=bool(payload.get("backend_available", False)),
            cups_available=bool(payload.get("cups_available", False)),
            default_printer=str(payload.get("default_printer", "") or ""),
            printers=printers,
            active_jobs=jobs,
            recent_jobs=[],
            alerts=alerts,
            diagnosis=str(payload.get("diagnosis", "") or ""),
            diagnosis_detail=str(payload.get("diagnosis_detail", "") or ""),
            refreshed_at_epoch=ts,
            raw_error=str(payload.get("raw_error", "") or ""),
        )
    except Exception:
        return None


def quick_probe_summary(timeout: float = 0.8) -> tuple[str, str, int, bool]:
    """Fast non-pycups status for bar usage.

    Returns: (default_printer, state_text, queue_count, attention)
    """
    default_name = ""
    state = "Unknown"
    queue_count = 0
    attention = False

    if shutil.which("lpstat") is None:
        return default_name, state, queue_count, True

    try:
        default_proc = subprocess.run(
            ["lpstat", "-d"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        line = (default_proc.stdout or "").strip()
        if "system default destination:" in line:
            default_name = line.split(":", 1)[-1].strip()
    except Exception:
        return default_name, "CUPS unavailable", queue_count, True

    try:
        queue_proc = subprocess.run(
            ["lpstat", "-o"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        rows = [line for line in (queue_proc.stdout or "").splitlines() if line.strip()]
        if default_name:
            queue_count = sum(1 for row in rows if row.startswith(default_name + "-"))
        else:
            queue_count = len(rows)
    except Exception:
        queue_count = 0

    if default_name:
        try:
            p = subprocess.run(
                ["lpstat", "-p", default_name],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            text = (p.stdout or "").lower()
            if " idle" in text:
                state = "Idle"
            elif "printing" in text:
                state = "Printing"
            elif "disabled" in text:
                state = "Paused"
                attention = True
            elif "unable" in text or "not accepting" in text:
                state = "Needs attention"
                attention = True
        except Exception:
            attention = True
            state = "CUPS unavailable"

    return default_name, state, queue_count, attention
