$ErrorActionPreference = 'Stop'
$prototype = Join-Path $PSScriptRoot 'SpectralKey/Prototypes/prototype 1/signal_observatory.py'
$runtime = $null
foreach ($name in @('py', 'python', 'python3')) {
    $command = Get-Command $name -ErrorAction SilentlyContinue
    if ($command -and $command.Source -notlike '*WindowsApps*') {
        $runtime = $command.Source
        break
    }
}
if (-not $runtime) {
    $bundled = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundled) { $runtime = $bundled }
}
if (-not $runtime) {
    throw 'Python 3.10 or newer is required. Install Python, then launch SpectralKey again.'
}
Write-Host 'Open http://127.0.0.1:8765 in your browser. Press Ctrl+C here to stop SpectralKey.'
& $runtime $prototype
exit $LASTEXITCODE
