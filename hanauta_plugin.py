#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget
from python_runtime import select_python_with_cups

PLUGIN_ROOT = Path(__file__).resolve().parent
POPUP_APP = PLUGIN_ROOT / "printer_popup.py"
SETTINGS_FILE = (
    Path.home() / ".local" / "state" / "hanauta" / "notification-center" / "settings.json"
)
SERVICE_KEY = "printer_widget"


DEFAULT_SERVICE = {
    "enabled": False,
    "show_in_notification_center": True,
    "show_in_bar": True,
    "bar_visibility_mode": "adaptive",
}

DEFAULT_WIDGET = {
    "notify_job_completed": True,
    "notify_job_failed": True,
    "notify_supply_alerts": True,
    "notify_printer_recovered": True,
}


def _save_settings(window) -> None:
    module = sys.modules.get(window.__class__.__module__)
    save_function = getattr(module, "save_settings_state", None) if module is not None else None
    if callable(save_function):
        save_function(window.settings_state)
        return
    callback = getattr(window, "_save_settings", None)
    if callable(callback):
        callback()


def _service_state(window) -> dict[str, object]:
    services = window.settings_state.setdefault("services", {})
    service = services.setdefault(SERVICE_KEY, dict(DEFAULT_SERVICE))
    if not isinstance(service, dict):
        service = dict(DEFAULT_SERVICE)
        services[SERVICE_KEY] = service
    for key, value in DEFAULT_SERVICE.items():
        service.setdefault(key, value)
    mode = str(service.get("bar_visibility_mode", "adaptive")).strip().lower()
    service["bar_visibility_mode"] = mode if mode in {"always", "adaptive"} else "adaptive"
    return service


def _widget_state(window) -> dict[str, object]:
    current = window.settings_state.setdefault(SERVICE_KEY, dict(DEFAULT_WIDGET))
    if not isinstance(current, dict):
        current = dict(DEFAULT_WIDGET)
        window.settings_state[SERVICE_KEY] = current
    # Backward-compat: keep honoring previous silent_success_notifications flag.
    if "silent_success_notifications" in current and "notify_job_completed" not in current:
        current["notify_job_completed"] = not bool(current.get("silent_success_notifications", False))
    for key, value in DEFAULT_WIDGET.items():
        current.setdefault(key, value)
    return current


def _persist_standalone_widget_settings(widget_state: dict[str, object]) -> None:
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    widget = payload.get(SERVICE_KEY, {})
    if not isinstance(widget, dict):
        widget = {}
    for key in DEFAULT_WIDGET.keys():
        widget[key] = bool(widget_state.get(key, DEFAULT_WIDGET[key]))
    payload[SERVICE_KEY] = widget
    try:
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _launch_popup(window, api: dict[str, object]) -> None:
    entry_command = api.get("entry_command")
    run_bg = api.get("run_bg")
    command: list[str] = []
    if callable(entry_command):
        try:
            command = list(entry_command(POPUP_APP))
        except Exception:
            command = []
    preferred_python = command[0] if command and str(command[0]).strip() else ""
    python_bin = select_python_with_cups(str(preferred_python))
    if not command:
        command = [python_bin, str(POPUP_APP)]
    elif str(command[0]).endswith("python") or "python" in str(Path(str(command[0])).name):
        command[0] = python_bin
    if callable(run_bg):
        try:
            run_bg(command)
        except Exception:
            pass
    status = getattr(window, "printer_widget_status", None)
    if isinstance(status, QLabel):
        status.setText("Printer widget opened.")


def _set_widget_setting(window, status: QLabel, key: str, value: object, message: str) -> None:
    state = _widget_state(window)
    state[key] = value
    _persist_standalone_widget_settings(state)
    _save_settings(window)
    status.setText(message)


def _set_bar_mode(window, status: QLabel, mode: str) -> None:
    service = _service_state(window)
    next_mode = "always" if str(mode).strip().lower() == "always" else "adaptive"
    service["bar_visibility_mode"] = next_mode
    _save_settings(window)
    if hasattr(window, "_notify_service_settings_changed"):
        try:
            window._notify_service_settings_changed()  # type: ignore[attr-defined]
        except Exception:
            pass
    if next_mode == "always":
        status.setText("Printer icon mode: always visible on bar.")
    else:
        status.setText("Printer icon mode: adaptive visibility.")


def build_printer_service_section(window, api: dict[str, object]) -> QWidget:
    SettingsRow = api["SettingsRow"]
    SwitchButton = api["SwitchButton"]
    ExpandableServiceSection = api["ExpandableServiceSection"]
    material_icon = api["material_icon"]
    icon_path = str(api.get("plugin_icon_path", "")).strip()

    service = _service_state(window)
    widget_cfg = _widget_state(window)

    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)

    bar_switch = SwitchButton(bool(service.get("show_in_bar", True)))
    bar_switch.toggledValue.connect(lambda enabled: window._set_service_bar_visibility(SERVICE_KEY, enabled))
    window.service_display_switches[SERVICE_KEY] = bar_switch
    layout.addWidget(
        SettingsRow(
            material_icon("widgets"),
            "Show on bar",
            "Display the compact printer chip on the top bar.",
            window.icon_font,
            window.ui_font,
            bar_switch,
        )
    )

    mode_combo = QComboBox()
    mode_combo.addItem("Adaptive visibility", "adaptive")
    mode_combo.addItem("Always show on bar", "always")
    current_mode = str(service.get("bar_visibility_mode", "adaptive")).strip().lower()
    mode_combo.setCurrentIndex(1 if current_mode == "always" else 0)

    status_label = QLabel("Printer widget is ready.")
    status_label.setWordWrap(True)
    status_label.setStyleSheet("color: rgba(246,235,247,0.72);")

    mode_combo.currentIndexChanged.connect(
        lambda _idx: _set_bar_mode(window, status_label, str(mode_combo.currentData() or "adaptive"))
    )
    layout.addWidget(
        SettingsRow(
            material_icon("visibility"),
            "Bar visibility behavior",
            "Adaptive visibility shows the chip only while printing or when attention is needed.",
            window.icon_font,
            window.ui_font,
            mode_combo,
        )
    )

    overview_switch = SwitchButton(bool(service.get("show_in_notification_center", True)))
    overview_switch.toggledValue.connect(
        lambda enabled: window._set_service_notification_visibility(SERVICE_KEY, enabled)
    )
    layout.addWidget(
        SettingsRow(
            material_icon("view_compact"),
            "Show in notification center",
            "Expose printer health in the notification center overview.",
            window.icon_font,
            window.ui_font,
            overview_switch,
        )
    )

    complete_switch = SwitchButton(bool(widget_cfg.get("notify_job_completed", True)))
    complete_switch.toggledValue.connect(
        lambda enabled: _set_widget_setting(
            window,
            status_label,
            "notify_job_completed",
            bool(enabled),
            "Print completion notifications updated.",
        )
    )
    layout.addWidget(
        SettingsRow(
            material_icon("notifications"),
            "Notify when job completes",
            "Show desktop notifications when print jobs complete successfully.",
            window.icon_font,
            window.ui_font,
            complete_switch,
        )
    )

    failed_switch = SwitchButton(bool(widget_cfg.get("notify_job_failed", True)))
    failed_switch.toggledValue.connect(
        lambda enabled: _set_widget_setting(
            window,
            status_label,
            "notify_job_failed",
            bool(enabled),
            "Failure notifications updated.",
        )
    )
    layout.addWidget(
        SettingsRow(
            material_icon("error"),
            "Notify on print failures",
            "Show desktop notifications for failed or canceled jobs.",
            window.icon_font,
            window.ui_font,
            failed_switch,
        )
    )

    supply_switch = SwitchButton(bool(widget_cfg.get("notify_supply_alerts", True)))
    supply_switch.toggledValue.connect(
        lambda enabled: _set_widget_setting(
            window,
            status_label,
            "notify_supply_alerts",
            bool(enabled),
            "Supply notifications updated.",
        )
    )
    layout.addWidget(
        SettingsRow(
            material_icon("inventory"),
            "Notify on paper/ink alerts",
            "Show desktop notifications for out-of-paper and toner/ink alerts.",
            window.icon_font,
            window.ui_font,
            supply_switch,
        )
    )

    recovered_switch = SwitchButton(bool(widget_cfg.get("notify_printer_recovered", True)))
    recovered_switch.toggledValue.connect(
        lambda enabled: _set_widget_setting(
            window,
            status_label,
            "notify_printer_recovered",
            bool(enabled),
            "Recovery notifications updated.",
        )
    )
    layout.addWidget(
        SettingsRow(
            material_icon("check"),
            "Notify when printer recovers",
            "Show desktop notifications when printer status returns to normal.",
            window.icon_font,
            window.ui_font,
            recovered_switch,
        )
    )

    open_button = QPushButton("Open printer widget")
    open_button.setObjectName("secondaryButton")
    open_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
    open_button.clicked.connect(lambda: _launch_popup(window, api))
    layout.addWidget(
        SettingsRow(
            material_icon("print"),
            "Open popup",
            "Open the printer popup for queue inspection and quick recovery actions.",
            window.icon_font,
            window.ui_font,
            open_button,
        )
    )

    cups_button = QPushButton("Open CUPS web")
    cups_button.setObjectName("secondaryButton")
    cups_button.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

    def _open_cups_web() -> None:
        try:
            subprocess.Popen(
                ["xdg-open", "http://localhost:631"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            status_label.setText("Opened CUPS web interface.")
        except Exception as exc:
            status_label.setText(f"Unable to open CUPS: {exc}")

    cups_button.clicked.connect(_open_cups_web)
    layout.addWidget(
        SettingsRow(
            material_icon("open_in_new"),
            "Open CUPS",
            "Open full printer administration in your browser.",
            window.icon_font,
            window.ui_font,
            cups_button,
        )
    )

    layout.addWidget(status_label)
    window.printer_widget_status = status_label

    section = ExpandableServiceSection(
        SERVICE_KEY,
        "Printer",
        "Default printer status, queue diagnostics, and quick recovery actions.",
        "?",
        window.icon_font,
        window.ui_font,
        content,
        window._service_enabled(SERVICE_KEY),
        lambda enabled: window._set_service_enabled(SERVICE_KEY, enabled),
        icon_path=icon_path,
    )
    window.service_sections[SERVICE_KEY] = section
    return section


def register_hanauta_plugin() -> dict[str, object]:
    return {
        "id": SERVICE_KEY,
        "name": "Printer",
        "service_sections": [
            {
                "key": SERVICE_KEY,
                "builder": build_printer_service_section,
                "supports_show_on_bar": True,
            }
        ],
    }
