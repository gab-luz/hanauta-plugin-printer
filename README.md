# Hanauta Printer Widget Plugin

Production-focused printer widget for Hanauta (PyQt6 + i3 + CUPS/IPP).

## Features

- Compact bar state:
  - default printer
  - state (idle / printing / warning / error)
  - queued jobs count
  - attention indicator
- Expandable popup panel:
  - diagnosis strip
  - default/current printer card
  - queue list with per-job cancel
  - pause/resume/cancel-my-jobs/set-default/open-CUPS actions
  - supplies/media section when available
  - alerts section with human-readable problem translation
- Non-blocking refresh:
  - polling on a background `QThread` every 5s
- Notifications:
  - job completed / failed
  - low supplies / paper out
  - printer recovered from bad state
  - optional silent mode for success notifications

## Files

- `hanauta_plugin.py`: Settings page integration (`supports_show_on_bar` included).
- `hanauta_bar_plugin.py`: compact bar integration + popup launcher.
- `printer_popup.py`: full popup UI.
- `printer_service.py`: CUPS/IPP service layer + action methods + cache.
- `printer_models.py`: typed dataclasses (`PrinterInfo`, `PrintJob`, `SupplyInfo`, `AlertItem`, `PrinterState`, `PrinterSnapshot`).
- `cups_monitor.py`: background workers and notification bridge.

## Dependencies

Debian 13:

```bash
sudo apt install python3-pyqt6 python3-cups cups cups-client
```

Optional but recommended for notifications:

```bash
sudo apt install libnotify-bin
```

## Run Popup Directly

```bash
python3 printer_popup.py
```

## Install Into Hanauta

Copy this folder to Hanauta plugins directory, for example:

```bash
cp -r $HOME/dev/hanauta-plugin-printer $HOME/.config/i3/hanauta/plugins/printer_widget
```

Then reopen Hanauta Settings and enable **Printer** service + **Show on bar**.

### Marketplace Dependency Prompt

This plugin includes `hanauta-install.json` + privileged install hooks so Marketplace will prompt for dependency installation during install finalization.

- Debian 13 / Debian-family: installs `python3-cups`
- Arch Linux: installs `python-pycups`

## Validation

Basic local checks:

```bash
python3 -m py_compile \
  printer_models.py \
  printer_service.py \
  cups_monitor.py \
  hanauta_plugin.py \
  hanauta_bar_plugin.py \
  printer_popup.py
```

## Quick CUPS Test Matrix

1. `lpstat -d` and `lpstat -p` should return printer data.
2. Send a test page from CUPS web UI (`http://localhost:631`) and confirm queue appears.
3. Pause printer in popup, then resume.
4. Cancel a queued job from popup.
5. Toggle default printer and verify compact bar updates.

## Notes

- If `python3-cups` is unavailable, the widget shows a degraded diagnosis state instead of freezing.
- Supply percentages are only shown when printer exposes marker levels via IPP/CUPS.
