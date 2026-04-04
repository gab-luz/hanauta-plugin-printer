#!/usr/bin/env python3
from __future__ import annotations

import subprocess
from typing import Callable

from PyQt6.QtCore import QThread, pyqtSignal

from printer_models import PrinterSnapshot
from printer_service import PrinterService, save_snapshot_cache


class SnapshotWorker(QThread):
    finished_snapshot = pyqtSignal(object)

    def __init__(self, service: PrinterService) -> None:
        super().__init__()
        self._service = service

    def run(self) -> None:
        snapshot = self._service.snapshot()
        save_snapshot_cache(snapshot)
        self.finished_snapshot.emit(snapshot)


class ActionWorker(QThread):
    finished_action = pyqtSignal(bool, str)

    def __init__(self, action: Callable[[], tuple[bool, str]]) -> None:
        super().__init__()
        self._action = action

    def run(self) -> None:
        ok = False
        message = "Action failed."
        try:
            ok, message = self._action()
        except Exception as exc:
            ok, message = False, f"Action failed: {exc}"
        self.finished_action.emit(bool(ok), str(message))


def send_widget_notification(title: str, body: str) -> None:
    try:
        subprocess.Popen(
            ["notify-send", "-a", "Hanauta Printer", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def _notif_enabled(settings: dict[str, object], key: str, default: bool) -> bool:
    return bool(settings.get(key, default)) if isinstance(settings, dict) else bool(default)


def emit_delta_notifications(
    previous: PrinterSnapshot | None,
    current: PrinterSnapshot,
    *,
    settings: dict[str, object] | None = None,
) -> None:
    if previous is None:
        return

    cfg = settings if isinstance(settings, dict) else {}
    notify_job_completed = _notif_enabled(cfg, "notify_job_completed", True)
    notify_job_failed = _notif_enabled(cfg, "notify_job_failed", True)
    notify_supply_alerts = _notif_enabled(cfg, "notify_supply_alerts", True)
    notify_printer_recovered = _notif_enabled(cfg, "notify_printer_recovered", True)

    prev_jobs = {job.job_id: job for job in previous.recent_jobs}
    curr_jobs = {job.job_id: job for job in current.recent_jobs}

    for job_id, job in curr_jobs.items():
        if job_id in prev_jobs:
            continue
        if job.state_text == "Completed" and notify_job_completed:
            send_widget_notification("Print job completed", f"{job.title} on {job.printer_name}")
        elif job.state_text in {"Failed", "Canceled"} and notify_job_failed:
            send_widget_notification("Print job failed", f"{job.title} ({job.state_text})")

    prev_alert_codes = {(alert.source, alert.code) for alert in previous.alerts}
    for alert in current.alerts:
        key = (alert.source, alert.code)
        if key in prev_alert_codes:
            continue
        if notify_supply_alerts and alert.code in {
            "marker-supply-low",
            "toner-low",
            "marker-supply-empty",
            "toner-empty",
            "media-empty",
            "media-needed",
        }:
            send_widget_notification(alert.message, alert.source or alert.detail)
        elif notify_job_failed and alert.code in {"job-failed", "printer-error"}:
            send_widget_notification(alert.message, alert.source or alert.detail)

    prev_state = {item.name: item.state.value for item in previous.printers}
    for printer in current.printers:
        old = prev_state.get(printer.name, "")
        if old in {"offline", "error", "paused", "stopped"} and printer.state.value in {"idle", "printing"}:
            if notify_printer_recovered:
                send_widget_notification("Printer available again", f"{printer.name} is {printer.state_text.lower()}.")

