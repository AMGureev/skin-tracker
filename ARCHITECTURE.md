# Skin Tracker: Legacy Tkinter Runtime

The supported entry point for this repair is `skin_tracker.py`, not `qt_tracker.py`.
`SkinTracker.spec` builds the Tkinter executable with TkAgg and excludes Qt.
The existing Qt implementation and its separate specification are left intact.

## Responsibilities

- `skin_tracker.py`: Tk widgets, portfolio workflows and UI-thread SQLite writes.
- `cstracker/background.py`: three daemon workers, keyed latest-job scheduling,
  result delivery through a queue polled by Tk, debouncing and shutdown.
- `cstracker/services/http.py`: thread-local HTTP sessions; rejects UI-thread requests.
- `cstracker/ui/market_actions.py`: asynchronous listing, removal and price dialogs.
- `cstracker/ui/price_chart.py`: persistent square canvas, reusable lines and bounded rendering.
- `cstracker/ui/charts.py`: throttled tooltips, bounded wheel zoom and panning.
- `cstracker/database.py`, `sales.py`, `currency.py`: existing persistence and calculations.
- `cstracker/diagnostics.py`: rotating error log with credential redaction.

Workers receive plain values captured before submission. They never use Tk variables,
call `root.after`, or use the main SQLite connection. UI callbacks are delivered only
from the Tk event loop. Pending jobs for the same item panel are replaced; stale running
results are discarded. Closing the application stops timers without waiting for HTTP.

Currency is refreshed in the background and cached, including a retry delay after failure.
Calculations never fetch a rate implicitly. Existing market snapshots provide offline
startup prices. Table updates retain row identities and selection; search is debounced.
Inventory history is cached by database revision and date rather than scanned on each
keystroke. Detail charts reuse their figure and canvas and keep extrema when reducing
long histories for display. The stored history itself is not reduced.

## Data and Build

`skins.db` remains beside the executable. The build does not package or replace it.
Tests and the executable self-test use temporary databases, not the user's portfolio.
Errors from the windowed executable go to `skin-tracker-errors.log` beside it.

```powershell
python -m pip install -r requirements-legacy.txt
python -m unittest discover -s tests -v
python skin_tracker.py --smoke-test --diagnostics-output legacy-smoke-source.json
python -m PyInstaller --noconfirm SkinTracker.spec
dist\SkinTracker.exe --smoke-test --diagnostics-output dist\legacy-smoke.json
```

The self-test exercises delayed market loading, event-loop heartbeat, selection
preservation and wheel zoom, then exits. Normal launch does not need any arguments.
