# TeraCota 2000

TeraCota 2000 is a Flask application for tracking automotive systems across
multiple customer locations. It combines operational records with summarized
measurement history while keeping the two data sets separated.

The production application is available at `https://teracota.matoug.com`.
Authentication is required before any application page or operational API can
be accessed.

## What the application tracks

- Systems grouped and ranked by location
- Operational status history and timeline events
- Shared site visits across systems at one location
- Issues shared by one or more systems
- Calibration and software updates
- Development ideas and tasks
- Deleted issue and visit recovery from the admin page
- Visitor logs for authenticated sessions
- Operational statistics
- Measurement summaries imported from production CSV files
- Source aliases that map one or more raw robot names to a TeraCota system

## Project layout

| Path | Purpose |
| --- | --- |
| `app.py` | Main Flask application, operations APIs, authentication, and schema initialization |
| `wsgi.py` | Gunicorn production entry point; initializes the databases before serving |
| `templates/` | Main application and login templates |
| `static/` | Main application JavaScript and CSS themes |
| `measurements/` | Measurement History package, APIs, importer, template, and browser assets |
| `measurement_uploader/` | Windows CSV uploader, example config, and scheduled-task installer |
| `tests/` | Automated integration tests |
| `deploy/` | systemd services, Nginx configuration, and Lightsail updater |
| `docs/` | Production installation and measurement upload runbooks |
| `External Project/` | Optional local, ignored reference material used during integration |

`External Project/` is not part of the running application or public repository.
It is ignored by Git; do not import from it or edit it as part of normal
TeraCota development.

The `measurement_uploader/` source files are intentionally versioned with the
server because the uploader and upload API must remain compatible. Lightsail
does not execute that directory. Only the example configuration is committed;
the real token-bearing config, receipt journal, logs, and lock files are ignored.

## Requirements

- Python 3.10 or newer
- A modern web browser
- Windows PowerShell for the provided local batch file and uploader utilities
- Ubuntu 24.04 for the documented Lightsail production deployment

The Python dependencies are declared in `requirements.txt`:

- Flask for the application
- Gunicorn for production on Ubuntu
- boto3 only when private S3 object storage is selected

## Local setup from a fresh clone

Open PowerShell in the directory where the project should be installed:

```powershell
git clone https://github.com/matoug24/teracota.git
Set-Location teracota
python -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8765` and use the development-only credentials:

```text
Username: teraview
Password: pythagorus
```

Stop the local server with `Ctrl+C`.

The supplied `run_teracota.bat` is a convenience launcher for the existing
Teraview Python installation at:

```text
C:\Program Files\Teraview\teracota_results_env_py3\python.exe
```

Use the virtual-environment commands above on a computer that does not have
that interpreter.

## Local configuration

Without environment variables, local development uses:

| Setting | Local default |
| --- | --- |
| Main database | `teracota.sqlite3` in the project root |
| Measurement data | `measurement_data/` in the project root |
| Username | `teraview` |
| Password | `pythagorus` |
| Demo records | Added only when the database is initially empty |
| Web address | `http://127.0.0.1:8765` |

`app.py` does not automatically read a local `.env` file. For a customized
local session, set variables in PowerShell before starting the app:

```powershell
$env:TERACOTA_USERNAME = "teraview"
$env:TERACOTA_PASSWORD = "a-local-development-password"
$env:TERACOTA_SECRET_KEY = "a-local-development-session-secret"
$env:TERACOTA_SEED_DEMO = "false"
$env:TERACOTA_MEASUREMENT_UPLOAD_TOKEN = "a-local-token-longer-than-24-characters"
python app.py
```

The production systemd service reads `/opt/teracota/.env` directly. See the
installation guide for production values and permissions.

## Data storage

TeraCota deliberately uses separate SQLite databases:

| Data | Default local path | Production path |
| --- | --- | --- |
| Operations, configuration, and logs | `teracota.sqlite3` | `/var/lib/teracota/teracota.sqlite3` |
| Measurement summaries | `measurement_data/vehicle_summaries.sqlite3` | `/var/lib/teracota/measurements/vehicle_summaries.sqlite3` |
| Upload receipts and import queue | `measurement_data/upload_control.sqlite3` | `/var/lib/teracota/measurements/upload_control.sqlite3` |
| Uploaded CSV objects | `measurement_data/object_store/` | `/var/lib/teracota/measurements/object_store/` |

The main database stores source mappings and location parsing configuration.
The measurement databases store compact summaries and upload state. Raw CSV
files are private filesystem objects by default, or private S3 objects when S3
is configured.

All runtime databases, measurement data, logs, secrets, private keys, and the
real uploader configuration are excluded by `.gitignore`.

## Main application routes

| Route | Purpose |
| --- | --- |
| `/` | Systems dashboard |
| `/systems/<id>` | System details |
| `/locations/<name>` | Location overview |
| `/measurements/system/<id>` | Measurement History filtered to one system |
| `/measurements/location/<name>` | Measurement History for all systems at a location |
| `/admin` | System administration, ordering, recovery, export, and measurement configuration |
| `/statistics` | Operational statistics |
| `/logs` | Authenticated visitor records |
| `/healthz` | Health response used by deployment checks |

Do not link `/admin` or `/logs` into the main navigation unless that product
decision changes. They are intentionally accessed by route.

## Measurement workflow

1. Create the location and systems in TeraCota.
2. Configure filename parsing and robot source aliases in `/admin`.
3. Run the Windows uploader on the production data computer.
4. The uploader sends stable CSV files through the token-protected upload API.
5. The Lightsail timer runs `python -m measurements.importer` every 15 minutes.
6. The importer stores compact summaries in the analytics database.
7. Users open Measurement History from a system or location page.

The uploader `client` must exactly match a TeraCota location name. Renaming a
location requires changing every uploader configuration for that location.

Full instructions are in `docs/MEASUREMENT_UPLOAD_GUIDE.md`.

## Run the automated checks

From the project root with the virtual environment active:

```powershell
python -m unittest tests.test_measurements -v
python -m compileall -q app.py wsgi.py gunicorn.conf.py measurements measurement_uploader tests
node --check static\js\app.js
node --check measurements\static\admin.js
node --check measurements\static\history.js
git diff --check
```

The JavaScript checks require Node.js. They can be skipped on a machine without
Node, but the Python integration tests should still be run before deployment.

## Production deployment

Production uses this request path:

```text
Browser -> Nginx HTTPS -> Gunicorn -> Flask -> SQLite
```

Use `docs/INSTALLATION_GUIDE.md` for a fresh Lightsail installation. After the
first installation of the updater, later releases are deployed with:

```bash
sudo update-teracota
```

Do not run the Flask development server publicly. Production must use the
provided Gunicorn systemd service and Nginx configuration.

## Documentation

- [Lightsail installation and operations guide](docs/INSTALLATION_GUIDE.md)
- [Measurement History uploader guide](docs/MEASUREMENT_UPLOAD_GUIDE.md)
