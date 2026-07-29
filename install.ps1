<#
    claude-subagents-effort -- installer entry point for Windows.

    This is deliberately not an `irm ... | iex` installer. It runs from a clone
    you already have on disk, so you can read what it does before you run it --
    which matters more than usual for something that rebuilds your coding agent.

        git clone https://github.com/pikalover6/claude-subagents-effort
        .\claude-subagents-effort\install.ps1

    Any argument is passed straight through to `python -m ccpatch`, so
    `.\install.ps1 --help` works, as does `.\install.ps1 --alias cc2 --yes`.
#>

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Find-Python {
    foreach ($candidate in @('python3', 'python', 'py')) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $found) { continue }
        try {
            & $found.Source -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $found.Source }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Write-Error @"
This installer needs Python 3.8 or newer, and could not find it.

  winget install Python.Python.3.12
  (or install from https://www.python.org/downloads/, ticking "Add to PATH")
"@
    exit 1
}

& $python -m ccpatch @args
exit $LASTEXITCODE
