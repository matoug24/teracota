# Measurement History Upload Guide

This runbook configures a Windows production computer to upload measurement
CSV files to TeraCota. The uploader sends only stable files over HTTPS, keeps a
local receipt journal, and does not delete or modify source files.

Production server: `https://teracota.matoug.com`

## 1. Understand the data flow

```text
Production result folders
  -> Windows measurement uploader
  -> HTTPS upload API
  -> private Lightsail filesystem or S3 object storage
  -> 15-minute measurement import timer
  -> compact measurement summary database
  -> Measurement History page
```

The maintenance database and measurement databases are separate. Measurement
configuration and robot aliases are stored in the main TeraCota database, while
summaries and upload receipts are stored under
`TERACOTA_MEASUREMENT_DATA_DIR`.

## 2. Prerequisites

Before configuring the production computer:

1. The TeraCota website must be reachable over valid HTTPS.
2. The customer location must already exist in TeraCota.
3. Every relevant robot must exist as a system under that location.
4. The server `.env` must contain a unique
   `TERACOTA_MEASUREMENT_UPLOAD_TOKEN` of at least 24 characters.
5. The main application and `teracota-measurement-import.timer` must be active.
6. The Windows computer must be able to read the result directory and reach
   `https://teracota.matoug.com`.

The uploader API token is not the website password. Keep it out of Git, email,
screenshots, and shared logs.

## 3. Configure the location in TeraCota

1. Sign in and open `https://teracota.matoug.com/admin`.
2. Find **Measurement History Configuration**.
3. Select the customer location.
4. Set the filename indexes used to extract Car ID and Body ID.
5. Confirm the layer names for 3-layer, 4-layer, and 5-layer measurements.
6. Map every raw CSV `Source` name to its TeraCota system.

One system can have multiple source aliases. For example, historical and new
serial-number names can both map to the same robot.

The uploader `client` value must exactly match the location name, including
spaces and capitalization. Unknown names are rejected. Source names that are
not mapped can be viewed at location level, but a system-specific Measurement
History page cannot include them until an alias is added.

## 4. Install the uploader files on Windows

Use a stable directory that ordinary cleanup will not remove:

```text
C:\ProgramData\TeraView\MeasurementUploader
```

Copy these three files from the repository's `measurement_uploader` directory:

```text
measurement_uploader.py
uploader_config.example.json
install_scheduled_task.ps1
```

For example, from a local repository checkout in an elevated PowerShell window:

```powershell
$Source = "C:\path\to\teracota\measurement_uploader"
$Target = "C:\ProgramData\TeraView\MeasurementUploader"
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item "$Source\measurement_uploader.py" $Target
Copy-Item "$Source\uploader_config.example.json" $Target
Copy-Item "$Source\install_scheduled_task.ps1" $Target
Copy-Item "$Target\uploader_config.example.json" "$Target\uploader_config.json"
```

`uploader_config.json` contains the API token and is intentionally ignored by
Git. Restrict access to the folder to the account that runs the scheduled task.

## 5. Configure the uploader

Edit `C:\ProgramData\TeraView\MeasurementUploader\uploader_config.json`:

```json
{
  "server_url": "https://teracota.matoug.com",
  "api_token": "THE_VALUE_FROM_TERACOTA_MEASUREMENT_UPLOAD_TOKEN",
  "client": "THE_EXACT_TERACOTA_LOCATION_NAME",
  "data_directory": "D:\\Production\\Results",
  "source_id": "production-pc-01",
  "scan_mode": "year_month",
  "recursive": false,
  "initial_lookback_days": 2,
  "rescan_overlap_days": 1,
  "bootstrap_all_history": false,
  "stable_seconds": 30,
  "request_timeout_seconds": 3600,
  "max_attempts": 3,
  "retry_delay_seconds": 600,
  "verify_tls": true,
  "expected_daily_files": 50,
  "journal_path": "C:\\ProgramData\\TeraView\\MeasurementUploader\\upload_journal.sqlite3",
  "log_path": "C:\\ProgramData\\TeraView\\MeasurementUploader\\uploader.log"
}
```

| Setting | Meaning |
| --- | --- |
| `server_url` | TeraCota HTTPS origin without a trailing path |
| `api_token` | Exact server upload token |
| `client` | Exact existing TeraCota location name |
| `data_directory` | Root folder containing production CSV results |
| `source_id` | Stable identifier for this production computer |
| `scan_mode` | `year_month` for `YYYY/MM` folders; use `root` for one flat root directory |
| `recursive` | Searches below each selected directory when `true` |
| `initial_lookback_days` | How far back the first normal scan looks |
| `rescan_overlap_days` | Rechecks recent date folders to catch late files |
| `bootstrap_all_history` | On the first scan only, scans every `YYYY/MM` directory |
| `stable_seconds` | Wait before confirming that file size and modification time stopped changing |
| `request_timeout_seconds` | Maximum duration of one server request |
| `max_attempts` | Upload retry count |
| `retry_delay_seconds` | Delay between retries |
| `verify_tls` | Must remain `true` in production |
| `expected_daily_files` | Warning threshold; it does not reject a smaller batch |
| `journal_path` | Local SQLite receipt journal used to avoid duplicate uploads |
| `log_path` | Rotating uploader log |

`data_directory` exists only on the production PC. It is not configured in the
TeraCota admin page and is never read directly by Lightsail.

CSV filenames should be unique within one client/location. The server identifies
imported jobs by client and filename.

## 6. Validate before uploading

Open PowerShell as Administrator:

```powershell
$Python = "C:\Program Files\Teraview\teracota_results_env_py3\python.exe"
$Folder = "C:\ProgramData\TeraView\MeasurementUploader"
$Config = "$Folder\uploader_config.json"

& $Python "$Folder\measurement_uploader.py" --config $Config --doctor
```

Expected result:

```text
OK: folder, journal, TLS, token, and server client are valid
```

The doctor checks the data folder, local journal, HTTPS connection, token, and
exact location name. Resolve every failure before uploading.

Preview which files would be selected without uploading:

```powershell
& $Python "$Folder\measurement_uploader.py" --config $Config --dry-run
```

## 7. Run the first upload

For only recent data, keep `bootstrap_all_history` set to `false` and run:

```powershell
& $Python "$Folder\measurement_uploader.py" --config $Config --verbose
```

For a one-time historical import of every `YYYY/MM` directory:

1. Confirm `scan_mode` is `year_month`.
2. Set `bootstrap_all_history` to `true` before the first successful scan.
3. Run the uploader manually with `--verbose`.
4. Confirm the batch imports successfully.
5. Set `bootstrap_all_history` back to `false`.

Large historical uploads may take time. Keep the PowerShell window open. The
uploader journal records successful files, so later runs skip unchanged files.

Check the local receipt status:

```powershell
& $Python "$Folder\measurement_uploader.py" --config $Config --status
Get-Content "$Folder\uploader.log" -Tail 100
```

Do not delete `upload_journal.sqlite3` during normal operation. It is the local
record that prevents unnecessary rediscovery and re-upload.

### Initial history from one PC, then ongoing uploads from another

It is safe to upload the historical archive from an engineering PC and then
hand ongoing uploads to the production PC.

On the engineering PC:

1. Copy or make the complete historical result directory available locally.
2. Create a dedicated config with the same `client` and `api_token` that the
   production PC will use.
3. Use a unique `source_id`, such as `engineering-pc-history`.
4. Use a journal path local to that PC.
5. For `YYYY/MM` folders, set `scan_mode` to `year_month` and
   `bootstrap_all_history` to `true`.
6. Run `--doctor`, then `--dry-run`, then the uploader with `--verbose`.
7. Trigger the Lightsail importer and verify the historical range in the website.
8. Stop using this config after the historical upload is complete.

One server batch can contain at most 1,000 CSV files. The uploader automatically
splits a larger discovery into consecutive batches of up to 1,000, verifies and
journals each completed batch, and then continues. If a later batch fails, rerun
the same command; previously verified files are skipped.

On the production PC:

1. Create a separate config with the same exact `client` and `api_token`.
2. Use a different stable `source_id`, such as `production-pc-01`.
3. Use a new journal stored on the production PC.
4. Keep `bootstrap_all_history` set to `false`.
5. For the first run, use an overlap such as `initial_lookback_days: 7` so files
   created while the historical upload was running are discovered.
6. Run `--doctor`, `--dry-run`, and one manual `--verbose` upload.
7. After confirming the handoff, install the daily scheduled task. The normal
   `rescan_overlap_days` setting handles late-arriving files thereafter.

Do not copy the engineering PC's uploader journal to the production PC unless
the absolute data paths are identical. Journal entries are keyed by local file
path. A new production journal is expected.

The overlap does not duplicate stored data. When the server sees the same
filename, size, and SHA-256 hash, it reuses the verified object instead of
uploading the content again. The importer replaces the summary for the same
client and filename, so processing remains idempotent.

Avoid running both uploaders on schedules after the cutover. Keep the
engineering config only as a recovery tool, with no scheduled task.

## 8. Trigger and verify the server import

The timer imports verified batches every 15 minutes. To process a test batch
immediately, run on Lightsail:

```bash
sudo systemctl start teracota-measurement-import.service
sudo systemctl status teracota-measurement-import.service --no-pager
sudo journalctl -u teracota-measurement-import.service -n 100 --no-pager
sudo systemctl list-timers teracota-measurement-import.timer --all
```

The service executes:

```bash
/opt/teracota/.venv/bin/python -m measurements.importer
```

Verify the server databases:

```bash
sudo -u teracota sqlite3 \
  /var/lib/teracota/measurements/upload_control.sqlite3 \
  'SELECT status,COUNT(*) FROM upload_batches GROUP BY status;'
sudo -u teracota sqlite3 \
  /var/lib/teracota/measurements/vehicle_summaries.sqlite3 \
  'SELECT client,COUNT(*) FROM jobs GROUP BY client;'
```

Then verify in the website:

1. Open the location page and select **Measurement History**.
2. Confirm jobs and measurements are greater than zero.
3. Confirm expected sources, colors, Car IDs, and Body IDs appear.
4. Open one system and confirm its view includes only mapped source aliases.
5. Review Operation Summary, Thickness, System Performance, and Miscellaneous Data.

## 9. Install the scheduled task

After a successful manual run:

```powershell
$Folder = "C:\ProgramData\TeraView\MeasurementUploader"
Set-ExecutionPolicy -Scope Process Bypass
& "$Folder\install_scheduled_task.ps1" `
  -ConfigPath "$Folder\uploader_config.json" `
  -DailyAt "01:00"
```

The installer runs the doctor before creating the task. The task starts missed
runs when the computer becomes available and prevents overlapping instances.

Verify it:

```powershell
Get-ScheduledTask -TaskName "TeraCota Measurement Upload"
Get-ScheduledTaskInfo -TaskName "TeraCota Measurement Upload"
Start-ScheduledTask -TaskName "TeraCota Measurement Upload"
Start-Sleep -Seconds 5
Get-Content "C:\ProgramData\TeraView\MeasurementUploader\uploader.log" -Tail 100
```

The task runs under the Windows account that installs it. That account needs
read access to the data directory and write access to the uploader folder.

## 10. Location rename procedure

Location renaming is available only in `/admin` because the location is also the
uploader client identifier.

When a location is renamed:

1. TeraCota moves existing measurement summaries and mappings to the new name.
2. Uploads using the old `client` value are rejected.
3. Update `client` in every affected `uploader_config.json`.
4. Run `--doctor` again.
5. Run one manual upload and confirm the server import.

Do not rename only the system. Source aliases remain associated with the system
record, while the uploader client always follows the location.

## 11. Troubleshooting

### Doctor reports an invalid client

- Confirm the location exists in the main dashboard.
- Copy the location name exactly from `/admin`.
- Check spaces and capitalization.
- If the location was renamed, update every production uploader.

### HTTP 401 or token failure

- Compare `api_token` with `TERACOTA_MEASUREMENT_UPLOAD_TOKEN` on Lightsail.
- Do not use the website login password.
- After changing the server `.env`, restart both workflows:

```bash
sudo systemctl restart teracota
sudo systemctl start teracota-measurement-import.service
```

### Files are found but Measurement History stays empty

Check the uploader receipt, import service, and both server databases:

```powershell
& $Python "$Folder\measurement_uploader.py" --config $Config --status
Get-Content "$Folder\uploader.log" -Tail 100
```

```bash
sudo journalctl -u teracota-measurement-import.service -n 150 --no-pager
sudo systemctl status teracota-measurement-import.timer --no-pager
curl -fsS http://127.0.0.1:8000/healthz
```

An `IMPORT_FAILED` message normally identifies a filename or CSV parsing issue.
After correcting the cause, retry failed server batches manually:

```bash
sudo -u teracota bash -lc \
  'cd /opt/teracota && set -a && source .env && set +a && .venv/bin/python -m measurements.importer --retry-failed'
```

### A system says its source is not mapped

Open `/admin`, find the raw source under the location's discovered sources, and
map it to the correct system. One system can have multiple aliases.

### The scheduled task did not run

```powershell
Get-ScheduledTask -TaskName "TeraCota Measurement Upload"
Get-ScheduledTaskInfo -TaskName "TeraCota Measurement Upload"
Get-Content "C:\ProgramData\TeraView\MeasurementUploader\uploader.log" -Tail 200
```

Confirm the configured Python path exists and the task account can access both
the result folder and uploader folder.

## 12. Backup responsibilities

The admin CSV export does not include raw measurement CSVs, compact summaries,
upload receipts, or uploader journals.

Back up these items separately:

- Lightsail `/var/lib/teracota/measurements/`
- The Windows `upload_journal.sqlite3`
- The Windows `uploader_config.json` in a secure secret store
- The original production result folders according to the site's retention policy

For filesystem storage, a complete measurement restore needs the analytics
database, upload-control database, and `object_store/` from the same backup.
For S3 storage, keep the S3 bucket private and enable an appropriate bucket
versioning and backup policy.
