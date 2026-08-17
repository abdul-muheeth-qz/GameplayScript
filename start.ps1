<#
Starts both the backend (python -m server, in server/.venv) and the frontend (npm run dev)
in their own windows. Run this from the repository root:

    .\start.ps1

python -m server must be run from the repo root (it's what puts server/ on the path), so the
backend window cd's to $PSScriptRoot before launching -- it does not cd into server/.
#>

$root = $PSScriptRoot
$venvPython = Join-Path $root "server\.venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    Write-Error "No venv at server\.venv -- run: python -m pip install -r server/requirements.txt (creating the venv first if needed)"
    exit 1
}

Start-Process pwsh -ArgumentList "-NoExit", "-Command", `
    "Set-Location '$root'; & '$venvPython' -m server"

Start-Process pwsh -ArgumentList "-NoExit", "-Command", `
    "Set-Location '$root\ui'; npm run dev"

Write-Host "Backend  -> http://127.0.0.1:8000 (see its own window)"
Write-Host "Frontend -> http://localhost:5173"
