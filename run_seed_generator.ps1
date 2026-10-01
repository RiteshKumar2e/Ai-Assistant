# Starts JUDO's seed generator in the background with no time limit — it runs
# until it reaches its target, backing off on errors and waiting out lost
# internet — unless one is already running (two copies would spend double
# quota and write duplicates).
# Runs at Windows logon via "JUDO Seed Generator.vbs" in the user's Startup
# folder (shell:startup) — delete that file to stop starting it at logon.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*core.seed_generator*' -and $_.CommandLine -notlike '*--status*' }
if ($running) { exit 0 }
$env:PYTHONIOENCODING = "utf-8"
Start-Process -FilePath python -ArgumentList "-u", "-m", "core.seed_generator", "--workers", "16", "--hours", "inf" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $root "memory\seed.log") `
    -RedirectStandardError (Join-Path $root "memory\seed.err.log")
