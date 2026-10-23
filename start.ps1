$ErrorActionPreference = 'Stop'
Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        throw 'Create .venv with Python 3.12+, then install requirements.txt (see README.md).'
    }
    & '.venv/Scripts/python.exe' -m uvicorn xarchive.app:create_app --factory --host 127.0.0.1 --port 8787 --workers 1 --no-access-log --timeout-graceful-shutdown 25
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
