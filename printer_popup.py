#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QSize
from PyQt6.QtGui import QColor, QCursor, QFont, QFontDatabase
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

PLUGIN_ROOT = Path(__file__).resolve().parent
ROOT_CANDIDATES = [
    PLUGIN_ROOT.parent,
    PLUGIN_ROOT.parents[2] if len(PLUGIN_ROOT.parents) > 2 else PLUGIN_ROOT,
    Path.home() / ".config" / "i3" / "hanauta",
]
ROOT = next((path for path in ROOT_CANDIDATES if (path / "src").exists()), ROOT_CANDIDATES[-1])
APP_DIR = ROOT / "src"
FONTS_DIR = ROOT / "assets" / "fonts"
SETTINGS_FILE = (
    Path.home() / ".local" / "state" / "hanauta" / "notification-center" / "settings.json"
)

if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from pyqt.shared.button_helpers import create_close_button
from pyqt.shared.theme import load_theme_palette, palette_mtime, rgba

from cups_monitor import ActionWorker, SnapshotWorker, emit_delta_notifications
from printer_models import AlertItem, AlertLevel, PrintJob, PrinterInfo, PrinterSnapshot, PrinterState
from printer_service import PrinterService

MATERIAL_GLYPHS = {
    "print": "\ue8ad",
    "error": "\ue000",
    "warning": "\ue002",
    "check": "\ue5ca",
    "pause": "\ue034",
    "resume": "\ue037",
    "cancel": "\ue5c9",
    "refresh": "\ue5d5",
    "open": "\ue89e",
    "expand_more": "\ue5cf",
    "expand_less": "\ue5ce",
    "inventory": "\ue179",
    "list": "\ue896",
}


def material_icon(name: str) -> str:
    return MATERIAL_GLYPHS.get(name, "?")


def load_fonts() -> dict[str, str]:
    loaded: dict[str, str] = {}
    font_map = {
        "ui": FONTS_DIR / "GoogleSans-Regular.ttf",
        "ui_medium": FONTS_DIR / "GoogleSans-Medium.ttf",
        "material": FONTS_DIR / "MaterialIcons-Regular.ttf",
    }
    for key, path in font_map.items():
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            loaded[key] = families[0]
    return loaded


def detect_font(*families: str) -> str:
    for family in families:
        if family and QFont(family).exactMatch():
            return family
    return "Sans Serif"


def load_widget_settings() -> dict[str, object]:
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    widget = payload.get("printer_widget", {})
    return widget if isinstance(widget, dict) else {}


class PrinterPopup(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.fonts = load_fonts()
        self.ui_font = detect_font(self.fonts.get("ui", ""), self.fonts.get("ui_medium", ""), "Google Sans", "Rubik", "Sans Serif")
        self.icon_font = detect_font(self.fonts.get("material", ""), "Material Icons", "Sans Serif")
        self.theme = load_theme_palette()
        self._theme_mtime = palette_mtime()

        self.service = PrinterService()
        self._snapshot_worker: SnapshotWorker | None = None
        self._action_worker: ActionWorker | None = None
        self._last_snapshot: PrinterSnapshot | None = None
        self._expanded = True

        self.setWindowTitle("Hanauta Printer")
        self.setObjectName("printerWindow")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame()
        self.card.setObjectName("printerCard")
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 14)
        shadow.setColor(Qt.GlobalColor.black)
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)

        shell = QVBoxLayout(self.card)
        shell.setContentsMargins(14, 14, 14, 12)
        shell.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title_icon = QLabel(material_icon("print"))
        title_icon.setObjectName("titleIcon")
        title_icon.setFixedSize(20, 20)
        title_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_icon.setFont(QFont(self.icon_font, 18))

        title = QLabel("Printer")
        title.setObjectName("titleLabel")
        title.setFont(QFont(self.ui_font, 13, QFont.Weight.DemiBold))

        self.subtitle = QLabel("Loading printer status...")
        self.subtitle.setObjectName("subtitleLabel")
        self.subtitle.setFont(QFont(self.ui_font, 10))

        titles = QVBoxLayout()
        titles.setContentsMargins(0, 0, 0, 0)
        titles.setSpacing(2)
        titles.addWidget(title)
        titles.addWidget(self.subtitle)

        self.expand_button = QPushButton(material_icon("expand_less"))
        self.expand_button.setObjectName("iconButton")
        self.expand_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.expand_button.setFont(QFont(self.icon_font, 17))
        self.expand_button.clicked.connect(self._toggle_expanded)

        self.refresh_button = QPushButton(material_icon("refresh"))
        self.refresh_button.setObjectName("iconButton")
        self.refresh_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.refresh_button.setFont(QFont(self.icon_font, 17))
        self.refresh_button.clicked.connect(self._start_refresh)

        close_button = create_close_button(material_icon("cancel"), self.icon_font, 17, object_name="iconButton")
        close_button.clicked.connect(self.close)

        header.addWidget(title_icon)
        header.addLayout(titles, 1)
        header.addWidget(self.expand_button)
        header.addWidget(self.refresh_button)
        header.addWidget(close_button)
        shell.addLayout(header)

        self.summary_card = QFrame()
        self.summary_card.setObjectName("summaryCard")
        summary_layout = QVBoxLayout(self.summary_card)
        summary_layout.setContentsMargins(10, 10, 10, 10)
        summary_layout.setSpacing(6)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)

        self.summary_state_icon = QLabel(material_icon("print"))
        self.summary_state_icon.setObjectName("summaryStateIcon")
        self.summary_state_icon.setFont(QFont(self.icon_font, 20))
        self.summary_state_icon.setFixedWidth(22)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.summary_printer_name = QLabel("No default printer")
        self.summary_printer_name.setObjectName("summaryPrinterName")
        self.summary_printer_name.setFont(QFont(self.ui_font, 11, QFont.Weight.DemiBold))

        self.summary_state_text = QLabel("Checking CUPS...")
        self.summary_state_text.setObjectName("summaryStateText")
        self.summary_state_text.setFont(QFont(self.ui_font, 9))

        text_col.addWidget(self.summary_printer_name)
        text_col.addWidget(self.summary_state_text)

        self.summary_queue_badge = QLabel("0")
        self.summary_queue_badge.setObjectName("queueBadge")
        self.summary_queue_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.summary_queue_badge.setMinimumWidth(22)

        self.summary_warning_badge = QLabel(material_icon("warning"))
        self.summary_warning_badge.setObjectName("warningBadge")
        self.summary_warning_badge.setFont(QFont(self.icon_font, 14))
        self.summary_warning_badge.setVisible(False)

        top.addWidget(self.summary_state_icon)
        top.addLayout(text_col, 1)
        top.addWidget(self.summary_warning_badge)
        top.addWidget(self.summary_queue_badge)
        summary_layout.addLayout(top)

        self.summary_busy = QProgressBar()
        self.summary_busy.setObjectName("summaryBusy")
        self.summary_busy.setRange(0, 0)
        self.summary_busy.setVisible(False)
        summary_layout.addWidget(self.summary_busy)

        shell.addWidget(self.summary_card)

        self.details_container = QWidget()
        details = QVBoxLayout(self.details_container)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(9)

        self.diagnosis_strip = QLabel("Loading diagnosis...")
        self.diagnosis_strip.setObjectName("diagnosisStrip")
        self.diagnosis_strip.setWordWrap(True)
        details.addWidget(self.diagnosis_strip)

        self.current_row_card = QFrame()
        self.current_row_card.setObjectName("controlCard")
        current_row = QHBoxLayout(self.current_row_card)
        current_row.setContentsMargins(10, 10, 10, 10)
        current_row.setSpacing(8)

        self.printer_combo = QComboBox()
        self.printer_combo.setObjectName("printerCombo")
        self.printer_combo.currentIndexChanged.connect(self._sync_current_printer_card)
        self.printer_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self.default_button = QPushButton("Set as default")
        self.default_button.setObjectName("secondaryButton")
        self.default_button.clicked.connect(self._set_selected_default)

        current_row.addWidget(self.printer_combo, 1)
        current_row.addWidget(self.default_button)
        details.addWidget(self.current_row_card)

        self.current_printer_card = QLabel("No printer selected.")
        self.current_printer_card.setObjectName("sectionCard")
        self.current_printer_card.setWordWrap(True)
        details.addWidget(self.current_printer_card)

        self.quick_actions_card = QFrame()
        self.quick_actions_card.setObjectName("controlCard")
        quick_row = QHBoxLayout(self.quick_actions_card)
        quick_row.setContentsMargins(10, 10, 10, 10)
        quick_row.setSpacing(6)

        self.pause_button = QPushButton("Pause")
        self.pause_button.setObjectName("secondaryButton")
        self.pause_button.clicked.connect(lambda: self._run_printer_action("pause"))

        self.resume_button = QPushButton("Resume")
        self.resume_button.setObjectName("secondaryButton")
        self.resume_button.clicked.connect(lambda: self._run_printer_action("resume"))

        self.cancel_all_button = QPushButton("Cancel My Jobs")
        self.cancel_all_button.setObjectName("secondaryButton")
        self.cancel_all_button.clicked.connect(self._cancel_my_jobs)

        self.open_cups_button = QPushButton("Open CUPS")
        self.open_cups_button.setObjectName("secondaryButton")
        self.open_cups_button.clicked.connect(self._open_cups)

        quick_row.addWidget(self.pause_button)
        quick_row.addWidget(self.resume_button)
        quick_row.addWidget(self.cancel_all_button)
        quick_row.addWidget(self.open_cups_button)
        details.addWidget(self.quick_actions_card)

        self.queue_card = QFrame()
        self.queue_card.setObjectName("controlCard")
        queue_shell = QVBoxLayout(self.queue_card)
        queue_shell.setContentsMargins(8, 8, 8, 8)
        queue_shell.setSpacing(0)

        self.queue_scroll = QScrollArea()
        self.queue_scroll.setObjectName("bodyScroll")
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.queue_host = QWidget()
        self.queue_layout = QVBoxLayout(self.queue_host)
        self.queue_layout.setContentsMargins(0, 0, 0, 0)
        self.queue_layout.setSpacing(6)
        self.queue_scroll.setWidget(self.queue_host)
        queue_shell.addWidget(self.queue_scroll)
        details.addWidget(self.queue_card, 1)

        self.supplies_card = QLabel("Supplies unavailable.")
        self.supplies_card.setObjectName("sectionCard")
        self.supplies_card.setWordWrap(True)
        details.addWidget(self.supplies_card)

        self.alerts_card = QLabel("No alerts yet.")
        self.alerts_card.setObjectName("sectionCard")
        self.alerts_card.setWordWrap(True)
        details.addWidget(self.alerts_card)

        self.status_label = QLabel("")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        details.addWidget(self.status_label)

        shell.addWidget(self.details_container, 1)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self._start_refresh)

        self.theme_timer = QTimer(self)
        self.theme_timer.setInterval(3000)
        self.theme_timer.timeout.connect(self._reload_theme_if_needed)

        self._apply_styles()
        self._position_window()
        self.refresh_timer.start()
        self.theme_timer.start()
        self._start_refresh()

    def _position_window(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(560, 700)
            return
        geo = screen.availableGeometry()
        width = min(640, max(500, int(geo.width() * 0.34)))
        height = min(760, max(560, int(geo.height() * 0.74)))
        x = geo.x() + geo.width() - width - 18
        y = geo.y() + 52
        self.setGeometry(x, y, width, height)

    def _toggle_expanded(self) -> None:
        self._expanded = not self._expanded
        self.details_container.setVisible(self._expanded)
        self.expand_button.setText(material_icon("expand_less" if self._expanded else "expand_more"))

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setProperty("error", bool(error))
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _clear_layout(self, layout: QVBoxLayout) -> None:
        while layout.count() > 0:
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _start_refresh(self) -> None:
        worker = self._snapshot_worker
        if isinstance(worker, SnapshotWorker) and worker.isRunning():
            return
        self._snapshot_worker = SnapshotWorker(self.service)
        self._snapshot_worker.finished_snapshot.connect(self._apply_snapshot)
        self._snapshot_worker.finished.connect(self._snapshot_worker.deleteLater)
        self._snapshot_worker.start()

    def _apply_snapshot(self, payload: object) -> None:
        if not isinstance(payload, PrinterSnapshot):
            self._set_status("Failed to parse printer snapshot.", error=True)
            self._snapshot_worker = None
            return
        widget_settings = load_widget_settings()
        emit_delta_notifications(
            self._last_snapshot,
            payload,
            silent_success=bool(widget_settings.get("silent_success_notifications", False)),
        )
        self._last_snapshot = payload

        self._render_summary(payload)
        self._render_printers(payload)
        self._render_queue(payload.active_jobs)
        self._render_supplies(payload)
        self._render_alerts(payload.alerts)

        self.diagnosis_strip.setText(f"{payload.diagnosis}\n{payload.diagnosis_detail}".strip())
        updated = datetime.fromtimestamp(max(0, payload.refreshed_at_epoch)).strftime("%H:%M:%S")
        self.subtitle.setText(f"Updated {updated}")

        if payload.ok:
            self._set_status("Ready.")
        else:
            self._set_status(payload.raw_error or payload.diagnosis_detail or "CUPS unavailable.", error=True)

        self._snapshot_worker = None

    def _summary_from_snapshot(self, snapshot: PrinterSnapshot) -> tuple[str, str, bool, int]:
        printer = next((item for item in snapshot.printers if item.is_default), None)
        if printer is None and snapshot.printers:
            printer = snapshot.printers[0]
        if printer is None:
            return "No default printer", "Unavailable", True, len(snapshot.active_jobs)
        return printer.name, printer.state_text, printer.needs_attention, printer.queue_count

    def _render_summary(self, snapshot: PrinterSnapshot) -> None:
        name, state_text, attention, queue_count = self._summary_from_snapshot(snapshot)
        self.summary_printer_name.setText(name)
        self.summary_state_text.setText(state_text)
        self.summary_warning_badge.setVisible(bool(attention))
        self.summary_queue_badge.setText(str(max(0, int(queue_count))))

        printing = any(item.state == PrinterState.PRINTING for item in snapshot.printers)
        if printing:
            self.summary_state_icon.setText(material_icon("print"))
            self.summary_busy.setVisible(True)
        elif attention:
            self.summary_state_icon.setText(material_icon("warning"))
            self.summary_busy.setVisible(False)
        else:
            self.summary_state_icon.setText(material_icon("check"))
            self.summary_busy.setVisible(False)

    def _render_printers(self, snapshot: PrinterSnapshot) -> None:
        current_name = str(self.printer_combo.currentData() or "")
        self.printer_combo.blockSignals(True)
        self.printer_combo.clear()
        for printer in snapshot.printers:
            badge = " (default)" if printer.is_default else ""
            self.printer_combo.addItem(f"{printer.name}{badge}", printer.name)
        if self.printer_combo.count() > 0:
            index = self.printer_combo.findData(current_name)
            if index < 0:
                default_index = next((i for i, item in enumerate(snapshot.printers) if item.is_default), 0)
                index = default_index
            self.printer_combo.setCurrentIndex(max(0, index))
        self.printer_combo.blockSignals(False)
        self._sync_current_printer_card()

    def _selected_printer(self) -> PrinterInfo | None:
        snapshot = self._last_snapshot
        if snapshot is None:
            return None
        selected = str(self.printer_combo.currentData() or "").strip()
        if not selected:
            return next(iter(snapshot.printers), None)
        for printer in snapshot.printers:
            if printer.name == selected:
                return printer
        return None

    def _sync_current_printer_card(self) -> None:
        printer = self._selected_printer()
        if printer is None:
            self.current_printer_card.setText("No printer selected.")
            return
        lines = [
            f"State: {printer.state_text}",
            f"Model: {printer.model or 'Unknown'}",
            f"Location: {printer.location or 'Not set'}",
            f"Queue jobs: {printer.queue_count}",
        ]
        if printer.reasons_text:
            lines.append("Reasons: " + ", ".join(printer.reasons_text))
        if printer.reasons_raw:
            lines.append("Raw: " + ", ".join(printer.reasons_raw))
        self.current_printer_card.setText("\n".join(lines))

    def _render_queue(self, jobs: list[PrintJob]) -> None:
        self._clear_layout(self.queue_layout)
        if not jobs:
            empty = QLabel("No jobs in queue.")
            empty.setObjectName("sectionCard")
            empty.setWordWrap(True)
            self.queue_layout.addWidget(empty)
            return
        for job in jobs:
            row = QFrame()
            row.setObjectName("queueRow")
            layout = QVBoxLayout(row)
            layout.setContentsMargins(10, 8, 10, 8)
            layout.setSpacing(4)

            header = QHBoxLayout()
            header.setContentsMargins(0, 0, 0, 0)
            header.setSpacing(8)

            title = QLabel(job.title)
            title.setFont(QFont(self.ui_font, 10, QFont.Weight.DemiBold))
            title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

            state = QLabel(f"{job.state_text}")
            state.setFont(QFont(self.ui_font, 9))

            cancel_button = QPushButton("Cancel")
            cancel_button.setObjectName("miniButton")
            cancel_button.clicked.connect(
                lambda _checked=False, jid=job.job_id, pname=job.printer_name: self._cancel_job(jid, pname)
            )

            header.addWidget(title)
            header.addWidget(state)
            header.addWidget(cancel_button)
            layout.addLayout(header)

            meta = QLabel(
                f"Job #{job.job_id} • {job.printer_name or 'Unknown printer'} • {job.user or 'unknown user'} • {max(0, job.age_seconds // 60)}m ago"
            )
            meta.setObjectName("queueMeta")
            meta.setWordWrap(True)
            layout.addWidget(meta)

            if job.progress_percent is not None:
                progress = QProgressBar()
                progress.setRange(0, 100)
                progress.setValue(max(0, min(100, int(job.progress_percent))))
                progress.setFormat(f"%p%")
                layout.addWidget(progress)

            self.queue_layout.addWidget(row)

    def _render_supplies(self, snapshot: PrinterSnapshot) -> None:
        printer = self._selected_printer()
        if printer is None or not printer.supplies:
            self.supplies_card.setText("Supplies and media details are unavailable for this printer.")
            return
        lines: list[str] = []
        for supply in printer.supplies:
            level = "unknown"
            if supply.level_percent is not None:
                level = f"{supply.level_percent}%"
            msg = f" • {supply.message}" if supply.message else ""
            lines.append(f"{supply.name}: {level}{msg}")
        if any("media" in reason.lower() for reason in printer.reasons_raw):
            lines.append("Tray/media warning: possible paper size/media mismatch.")
        self.supplies_card.setText("\n".join(lines))

    def _render_alerts(self, alerts: list[AlertItem]) -> None:
        if not alerts:
            self.alerts_card.setText("No recent alerts.")
            return
        lines: list[str] = []
        for alert in alerts[:7]:
            prefix = "INFO"
            if alert.level == AlertLevel.WARNING:
                prefix = "WARN"
            elif alert.level == AlertLevel.ERROR:
                prefix = "ERROR"
            line = f"[{prefix}] {alert.message}"
            if alert.source:
                line += f" ({alert.source})"
            if alert.detail:
                line += f" — {alert.detail}"
            lines.append(line)
        self.alerts_card.setText("\n".join(lines))

    def _run_action(self, action) -> None:
        worker = self._action_worker
        if isinstance(worker, ActionWorker) and worker.isRunning():
            return
        self._action_worker = ActionWorker(action)
        self._action_worker.finished_action.connect(self._on_action_result)
        self._action_worker.finished.connect(self._action_worker.deleteLater)
        self._action_worker.start()

    def _on_action_result(self, ok: bool, message: str) -> None:
        self._set_status(message, error=not ok)
        self._action_worker = None
        self._start_refresh()

    def _run_printer_action(self, kind: str) -> None:
        printer = self._selected_printer()
        if printer is None:
            self._set_status("Select a printer first.", error=True)
            return
        if kind == "pause":
            self._run_action(lambda: self.service.pause_printer(printer.name))
            return
        if kind == "resume":
            self._run_action(lambda: self.service.resume_printer(printer.name))
            return

    def _cancel_job(self, job_id: int, printer_name: str = "") -> None:
        self._run_action(lambda: self.service.cancel_job(job_id, printer_name))

    def _cancel_my_jobs(self) -> None:
        printer = self._selected_printer()
        printer_name = printer.name if printer is not None else ""
        self._run_action(lambda: self.service.cancel_my_jobs(printer_name=printer_name))

    def _set_selected_default(self) -> None:
        printer = self._selected_printer()
        if printer is None:
            self._set_status("Select a printer first.", error=True)
            return
        self._run_action(lambda: self.service.set_default_printer(printer.name))

    def _open_cups(self) -> None:
        try:
            subprocess.Popen(
                ["xdg-open", "http://localhost:631"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            self._set_status("Opened CUPS web interface.")
        except Exception as exc:
            self._set_status(f"Unable to open CUPS: {exc}", error=True)

    def _reload_theme_if_needed(self) -> None:
        mtime = palette_mtime()
        if mtime <= self._theme_mtime:
            return
        self._theme_mtime = mtime
        self.theme = load_theme_palette()
        self._apply_styles()

    def _apply_styles(self) -> None:
        theme = self.theme
        bg = rgba(theme.background, 0.98)
        card_bg = rgba(theme.surface, 0.84)
        text = theme.text
        subdued = rgba(theme.text, 0.66)
        accent = theme.primary
        warning = "#f4bd4a"

        self.setStyleSheet(
            f"""
            QWidget#printerWindow {{ background: transparent; }}
            QFrame#printerCard {{
                background: {bg};
                border: 1px solid {rgba(theme.outline, 0.35)};
                border-radius: 22px;
            }}
            QLabel#titleIcon {{ color: {accent}; }}
            QLabel#titleLabel {{ color: {text}; }}
            QLabel#subtitleLabel {{ color: {subdued}; }}
            QPushButton#iconButton {{
                min-width: 30px;
                min-height: 30px;
                border-radius: 10px;
                border: 1px solid {rgba(theme.outline, 0.25)};
                background: {rgba(theme.surface_container_high, 0.42)};
                color: {text};
            }}
            QPushButton#iconButton:hover {{ background: {rgba(theme.surface_container_high, 0.62)}; }}
            QFrame#summaryCard, QLabel#sectionCard {{
                border-radius: 14px;
                border: 1px solid {rgba(theme.outline, 0.22)};
                background: {card_bg};
                color: {text};
                padding: 8px;
            }}
            QFrame#controlCard {{
                border-radius: 14px;
                border: 1px solid {rgba(theme.outline, 0.22)};
                background: {rgba(theme.surface_container_high, 0.42)};
            }}
            QLabel#summaryStateIcon {{ color: {accent}; }}
            QLabel#summaryPrinterName {{ color: {text}; }}
            QLabel#summaryStateText {{ color: {subdued}; }}
            QLabel#queueBadge {{
                background: {rgba(accent, 0.3)};
                border: 1px solid {rgba(accent, 0.6)};
                border-radius: 10px;
                color: {text};
                padding: 1px 6px;
                font-weight: 600;
            }}
            QLabel#warningBadge {{ color: {warning}; }}
            QLabel#diagnosisStrip {{
                border-radius: 10px;
                border: 1px solid {rgba(warning, 0.6)};
                background: {rgba(warning, 0.12)};
                color: {text};
                padding: 8px;
            }}
            QFrame#queueRow {{
                border-radius: 12px;
                border: 1px solid {rgba(theme.outline, 0.22)};
                background: {rgba(theme.surface_container, 0.72)};
            }}
            QLabel#queueMeta {{ color: {subdued}; font-size: 11px; }}
            QLabel#statusLabel {{ color: {subdued}; }}
            QLabel#statusLabel[error='true'] {{ color: #ffb4ab; }}
            QPushButton#secondaryButton, QPushButton#miniButton {{
                border-radius: 10px;
                border: 1px solid {rgba(theme.outline, 0.32)};
                background: {rgba(theme.surface_container_high, 0.52)};
                color: {text};
                min-height: 32px;
                padding: 0 10px;
            }}
            QPushButton#miniButton {{ min-height: 28px; padding: 0 8px; }}
            QPushButton#secondaryButton:hover, QPushButton#miniButton:hover {{
                background: {rgba(theme.surface_container_high, 0.68)};
            }}
            QComboBox#printerCombo {{
                min-height: 34px;
                border-radius: 10px;
                border: 1px solid {rgba(theme.outline, 0.32)};
                background: {rgba(theme.surface_container_high, 0.46)};
                color: {text};
                padding: 0 8px;
            }}
            QScrollArea#bodyScroll {{ background: transparent; border: none; }}
            QScrollArea#bodyScroll > QWidget > QWidget {{
                background: transparent;
            }}
            QProgressBar#summaryBusy {{
                border: 1px solid {rgba(accent, 0.45)};
                border-radius: 6px;
                background: {rgba(theme.surface_container_high, 0.35)};
                min-height: 8px;
                max-height: 8px;
            }}
            QProgressBar#summaryBusy::chunk {{
                background: {accent};
                border-radius: 6px;
            }}
            """
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    popup = PrinterPopup()
    popup.show()

    signal.signal(signal.SIGINT, lambda signum, frame: app.quit())
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
