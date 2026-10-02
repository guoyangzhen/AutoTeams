# Package the same Local Runner release for both static deployment targets.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $root 'local-runner'
$targets = @(
    (Join-Path $root 'frontend\public\local-runner'),
    (Join-Path $root 'deploy\cloudflare\local-runner')
)
$items = @('package.json', 'package-lock.json', 'tsconfig.json', 'src', 'bin')
foreach ($item in ($items + 'install.ps1')) {
    if (-not (Test-Path -LiteralPath (Join-Path $runner $item))) {
        throw "Runner distribution is missing $item"
    }
}
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$stage = Join-Path $tempRoot ("autoteams-localrunner-" + [guid]::NewGuid().ToString('N'))
$stageFull = [IO.Path]::GetFullPath($stage)
if (-not $stageFull.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $stageFull -eq $tempRoot) { throw "Unsafe staging path: $stageFull" }
$zip = Join-Path $stage 'local-runner.zip'
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($item in $items) {
        Copy-Item -LiteralPath (Join-Path $runner $item) -Destination $stage -Recurse
    }
    $zipItems = foreach ($item in $items) { Join-Path $stage $item }
    Compress-Archive -LiteralPath $zipItems -DestinationPath $zip
    foreach ($target in $targets) {
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        Copy-Item -LiteralPath $zip -Destination (Join-Path $target 'local-runner.zip') -Force
        Copy-Item -LiteralPath (Join-Path $runner 'install.ps1') -Destination (Join-Path $target 'install.ps1') -Force
        Write-Host "Packaged Local Runner in $target"
    }
} finally {
    if (Test-Path -LiteralPath $stageFull) {
        Remove-Item -LiteralPath $stageFull -Recurse -Force
    }
}
