param([switch]$WithAsr)
$ErrorActionPreference = 'Stop'
$setupArgs = @((Join-Path $PSScriptRoot 'setup.py'))
if ($WithAsr) { $setupArgs += '--with-asr' }
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python @setupArgs
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3.12 @setupArgs
} else { throw 'Python 3.11+ is required; Python 3.12 is recommended.' }
if ($LASTEXITCODE -ne 0) { throw 'Dependency setup failed.' }
