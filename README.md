# Skin Tracker

Desktop portfolio tracker for CS skins and other assets, including market
quotes, inventory history, profit calculations, partial sales and item charts.

The maintained entry point is **`skin_tracker.py` (Tkinter)**. The PySide6
implementation remains in the repository as experimental code; it is not the
recommended application or the default Windows build.

## Run

Python 3.12 and a Python installation with Tk support are recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-legacy.txt
python skin_tracker.py
```

On Linux, install your distribution's Tk package if it is not included with Python.

## Private Configuration

Market.csgo credentials can be entered in the application's Settings window.
Alternatively, set environment variables before starting the application:

```powershell
$env:CSMARKET_API_KEY = "your-market-api-key"
$env:LIS_SKINS_API_KEY = "your-lis-skins-api-key"
python skin_tracker.py
```

For local file-based configuration, use the structure in `config.example.json`
in a file named `config.local.json` in the project root. For the packaged EXE,
place this private file beside the executable. Environment variables take
precedence over the local file. Do not commit real API keys.

The application creates `skins.db` automatically. The database contains private
portfolio data and may contain credentials saved through Settings. Databases,
exports, private configuration, logs and packaged applications are excluded from Git.

## Features

- Inventory and asset tables with search, filtering and selection.
- Market buy/sell valuation, commission-aware profit and USD accounting.
- Full and partial sales with preserved cost basis and sale exchange rates.
- Item images and local market/Lis Skins price-history snapshots.
- Interactive charts with tooltips, range controls and wheel zoom.
- Background network jobs and visible loading indicators.

Price history is collected from available API responses and local snapshots.
An API connection does not imply that historical Lis Skins prices are available
for dates before collection started.

## Test

The legacy suite does not require PySide6 or production API keys:

```powershell
python -m unittest discover -s tests -p "test_legacy*.py" -v
python skin_tracker.py --smoke-test --diagnostics-output legacy-smoke-source.json
```

The executable smoke test uses a temporary database and simulated delayed
network responses; it does not open the user's portfolio.

To run the experimental Qt repository/model tests as well, install
`requirements.txt` and run `python -m unittest discover -s tests -v`.

## Windows Build

```powershell
python -m PyInstaller --noconfirm SkinTracker.spec
.\dist\SkinTracker.exe --smoke-test --diagnostics-output dist\legacy-smoke.json
```

The build produces `dist/SkinTracker.exe` using TkAgg, without PySide6.
It does not bundle or replace `skins.db`. Keep your existing database beside
the executable and maintain a separate backup.

See [ARCHITECTURE.md](ARCHITECTURE.md) for module responsibilities and threading rules.
