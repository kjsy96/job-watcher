@echo off
rem Job Watcher daily run, started by Task Scheduler (see README, "Scheduled daily run").
rem Task Scheduler starts this every hour and at logon; "jobwatcher run" itself makes
rem sure only one real run happens per day, and that being offline doesn't use it up.
rem Every attempt is appended to logs\run.log. The exit code is run's:
rem   0 ok or already ran today, 1 some companies failed, 2 config/database problem,
rem   3 offline (today not counted, so the next attempt retries).
rem After run, it also starts "jobwatcher discover" if config\discovery.toml exists.
rem discover counts at most one run per week the same way, so most attempts skip it
rem at once. Its output and exit code go to the log; the task's exit code stays run's.

setlocal
rem Work from the repo folder (this script lives in its "scripts" subfolder), so the
rem default paths config\, data\ and reports\ resolve correctly.
cd /d "%~dp0.."

rem Task Scheduler's environment may not include the user's PATH, so fall back to
rem uv's default per-user install location.
set "UV=uv"
where uv >nul 2>nul || set "UV=%USERPROFILE%\.local\bin\uv.exe"

if not exist logs mkdir logs
echo ===== %DATE% %TIME% >> logs\run.log
rem --frozen: use the committed uv.lock exactly; never update dependencies on a
rem scheduled run.
"%UV%" run --frozen python -m jobwatcher run >> logs\run.log 2>&1
set "CODE=%ERRORLEVEL%"
echo exit code %CODE% >> logs\run.log

rem Skip discovery when run found the laptop offline (exit 3): it would only fail.
if "%CODE%"=="3" goto done
if not exist config\discovery.toml goto done
echo ----- discover >> logs\run.log
"%UV%" run --frozen python -m jobwatcher discover >> logs\run.log 2>&1
echo discover exit code %ERRORLEVEL% >> logs\run.log

:done
exit /b %CODE%
