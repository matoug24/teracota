param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$PythonExe = "C:\Program Files\Teraview\teracota_env_py3\python.exe",
    [string]$TaskName = "TeraCota Measurement Upload",
    [string]$DailyAt = "01:00",
    [string]$TaskUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name,
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

$ScheduledPython = Join-Path (Split-Path -Parent $PythonExe) "pythonw.exe"
if (-not (Test-Path -LiteralPath $ScheduledPython -PathType Leaf)) {
    $ScheduledPython = $PythonExe
}

$action = New-ScheduledTaskAction -Execute $ScheduledPython -Argument ('"{0}" --config "{1}"' -f $ResolvedScript, $ResolvedConfig)
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Uploads verified measurement CSVs to TeraCota Measurement History." -Force
Write-Host "Scheduled task '$TaskName' installed for $DailyAt under $TaskUser using $ScheduledPython."
