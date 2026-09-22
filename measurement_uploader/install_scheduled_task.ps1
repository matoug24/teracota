param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$PythonExe = "C:\Program Files\Teraview\teracota_results_env_py3\python.exe",
    [string]$TaskName = "TeraCota Measurement Upload",
    [string]$DailyAt = "01:00",
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
$ScriptPath = Join-Path $PSScriptRoot "measurement_uploader.py"
$ResolvedConfig = (Resolve-Path -LiteralPath $ConfigPath).Path
$ResolvedScript = (Resolve-Path -LiteralPath $ScriptPath).Path

if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    throw "Python executable not found: $PythonExe"
}
if (-not $SkipDoctor) {
    & $PythonExe $ResolvedScript --config $ResolvedConfig --doctor
    if ($LASTEXITCODE -ne 0) { throw "Uploader diagnostics failed" }
}

$action = New-ScheduledTaskAction -Execute $PythonExe -Argument ('"{0}" --config "{1}"' -f $ResolvedScript, $ResolvedConfig)
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description "Uploads verified measurement CSVs to TeraCota Measurement History." -Force
Write-Host "Scheduled task '$TaskName' installed for $DailyAt."
