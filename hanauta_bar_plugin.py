#!/usr/bin/env python3
from __future__ import annotations

import time
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from printer_models import PrinterSnapshot
from printer_service import load_snapshot_cache, quick_probe_summary

SERVICE_KEY = "printer_widget"
PROCESS_ATTR = "_plugin_printer_popup_process"
CACHE_ATTR = "_plugin_printer_cached_snapshot"
CACHE_TS_ATTR = "_plugin_printer_cached_snapshot_ts"
GLYPH_DEFAULT = "\ue8ad"  # print
GLYPH_WARNING = "\ue002"  # warning
GLYPH_ERROR = "\ue000"  # error


def _service_settings(load_service_settings):
    services = load_service_settings()
    current = services.get(SERVICE_KEY, {}) if isinstance(services, dict) else {}
    return current if isinstance(current, dict) else {}


def _short_state(snapshot: PrinterSnapshot | None) -> tuple[str, str, int, bool]:
    if snapshot is None:
        return "", "Unknown", 0, True
    default = snapshot.default_printer
    active_jobs = len(snapshot.active_jobs)
    if not default and snapshot.printers:
        default = snapshot.printers[0].name
    attention = any(item.needs_attention for item in snapshot.printers)
    state = "Idle"
    for printer in snapshot.printers:
        if printer.is_default or printer.name == default:
            state = printer.state_text
            active_jobs = printer.queue_count if printer.queue_count >= 0 else active_jobs
            attention = printer.needs_attention
            break
    return default, state, active_jobs, attention


def register_hanauta_bar_plugin(bar, api: dict[str, object]) -> None:
    plugin_dir = Path(str(api.get("plugin_dir", ""))).expanduser()
    popup_path = plugin_dir / "printer_popup.py"
    if not popup_path.exists():
        return

    add_status_button = api["add_status_button"]
    toggle_singleton_process = api["toggle_singleton_process"]
    sync_popup_button = api["sync_popup_button"]
    load_service_settings = api["load_service_settings"]
    register_hook = api["register_hook"]

    def on_click() -> None:
        active = bool(
            toggle_singleton_process(
                PROCESS_ATTR,
                popup_path,
                python_bin=bar._python_bin(),
            )
        )
        button.setChecked(active)

    button = add_status_button(
        SERVICE_KEY,
        GLYPH_DEFAULT,
        tooltip="Printer",
        checkable=True,
        on_click=on_click,
        font_size=16,
    )

    def _sync_visibility() -> None:
        current = _service_settings(load_service_settings)
        enabled = bool(current.get("enabled", False))
        show_in_bar = bool(current.get("show_in_bar", False))
        button.setVisible(enabled and show_in_bar)

    def _refresh_cached_snapshot(force: bool = False) -> PrinterSnapshot | None:
        now = time.time()
        last = float(getattr(bar, CACHE_TS_ATTR, 0.0) or 0.0)
        cached = getattr(bar, CACHE_ATTR, None)
        if (not force) and (now - last) < 5.0 and isinstance(cached, PrinterSnapshot):
            return cached
        snapshot = load_snapshot_cache(max_age_seconds=30)
        setattr(bar, CACHE_ATTR, snapshot)
        setattr(bar, CACHE_TS_ATTR, now)
        return snapshot

    def _sync_compact_state() -> None:
        snapshot = _refresh_cached_snapshot()
        if snapshot is not None:
            default_name, state, queue_count, attention = _short_state(snapshot)
        else:
            default_name, state, queue_count, attention = quick_probe_summary(timeout=0.6)

        if state.lower() in {"error", "stopped", "offline", "cups unavailable"}:
            glyph = GLYPH_ERROR
        elif attention:
            glyph = GLYPH_WARNING
        else:
            glyph = GLYPH_DEFAULT

        badge = str(max(0, int(queue_count)))
        button.setText(glyph if badge == "0" else f"{glyph}{badge}")

        printer_label = default_name or "No default"
        tooltip = f"Printer: {printer_label}\nState: {state}\nQueued jobs: {badge}"
        if attention:
            tooltip += "\nNeeds attention"
        sync_popup_button(
            button,
            PROCESS_ATTR,
            popup_path,
            tooltip=tooltip,
        )

    def on_close() -> None:
        process = getattr(bar, PROCESS_ATTR, None)
        if process is not None and process.poll() is None:
            process.terminate()

    register_hook("settings_reloaded", _sync_visibility)
    register_hook("poll", _sync_compact_state)
    register_hook("close", on_close)

    _sync_visibility()
    _sync_compact_state()
