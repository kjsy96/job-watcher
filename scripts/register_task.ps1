<#
.SYNOPSIS
    Create (or update) the Windows scheduled task that runs Job Watcher daily.

.DESCRIPTION
    Registers a task for the current user, as specified in PROJECT_PLAN.md issue 2.7:
      - triggers at logon and every hour
      - starts only if a network connection is available
      - runs as soon as possible after a missed start
      - never wakes the computer; runs on battery
      - stops after 30 minutes if it hangs; never runs two copies at once
      - runs with no window (conhost --headless), so nothing flashes every hour

    The hourly triggers only give the program chances to run. "jobwatcher run" makes
    sure one real run happens per day and that being offline doesn't use it up.

.PARAMETER DryRun
    Show what would be registered, without changing anything.

.PARAMETER Remove
    Delete the task.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -DryRun
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1 -Remove
#>
param(
    [switch]$DryRun,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$TaskName = "Job Watcher daily run"
$TaskPath = "\JobWatcher\"

if ($Remove) {
    if ($DryRun) {
        Write-Output "Would remove task $TaskPath$TaskName (if it exists)."
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -Confirm:$false
    Write-Output "Removed task $TaskPath$TaskName."
    exit 0
}

$repo = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $PSScriptRoot "run_daily.bat"
if (-not (Test-Path $bat)) { throw "run_daily.bat not found next to this script: $bat" }
$user = "$env:USERDOMAIN\$env:USERNAME"

# conhost --headless runs the batch file with no console window.
$action = New-ScheduledTaskAction `
    -Execute "$env:WINDIR\System32\conhost.exe" `
    -Argument "--headless `"$bat`"" `
    -WorkingDirectory $repo

$atLogon = New-ScheduledTaskTrigger -AtLogOn -User $user
# Every hour, starting from midnight today, with no end date.
$hourly = New-ScheduledTaskTrigger -Once -At (Get-Date).Date `
    -RepetitionInterval (New-TimeSpan -Hours 1)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew
# WakeToRun is off by default: the task never wakes the computer.

# Runs as the current user, only while logged on, without admin rights.
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited

$task = New-ScheduledTask -Action $action -Trigger @($atLogon, $hourly) `
    -Settings $settings -Principal $principal `
    -Description "Job Watcher: fetch target job boards, filter new postings, write reports\YYYY-MM-DD.md. One real run per day; see the repo README."

if ($DryRun) {
    Write-Output "Dry run: nothing registered. Would register $TaskPath$TaskName for $user :"
    Write-Output "  Action:    $($action.Execute) $($action.Arguments)"
    Write-Output "  Start in:  $($action.WorkingDirectory)"
    Write-Output "  Triggers:  at logon; every hour from $((Get-Date).Date.ToString('yyyy-MM-dd HH:mm'))"
    # Build the line first: "Write-Output (...) -f ..." would pass -f as a separate argument.
    $line = "  Settings:  network required={0}, start when available={1}, wake to run={2}, " +
        "time limit={3}, multiple instances={4}"
    $line = $line -f $settings.RunOnlyIfNetworkAvailable, $settings.StartWhenAvailable,
        $settings.WakeToRun, $settings.ExecutionTimeLimit, $settings.MultipleInstances
    Write-Output $line
    exit 0
}

Register-ScheduledTask -TaskName $TaskName -TaskPath $TaskPath -InputObject $task -Force | Out-Null
Write-Output "Registered task $TaskPath$TaskName for $user."
Write-Output "It runs at logon and hourly; see logs\run.log and reports\ in $repo."
