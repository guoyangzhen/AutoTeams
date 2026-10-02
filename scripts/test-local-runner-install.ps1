$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$fixture = Join-Path $tempRoot ("autoteams-installer-test-" + [guid]::NewGuid().ToString('N'))
if (-not ([IO.Path]::GetFullPath($fixture)).StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe fixture path' }
$oldPath = $env:PATH
$oldProfile = $env:USERPROFILE
try {
    $source = Join-Path $fixture 'local-runner'
    $stub = Join-Path $fixture 'stub'
    $fixtureProfile = Join-Path $fixture '用户配置'
    New-Item -ItemType Directory -Force -Path $source, $stub, $fixtureProfile, (Join-Path $source 'src'), (Join-Path $source 'bin') | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'local-runner/install.ps1') -Destination $source
    foreach ($name in @('package.json', 'package-lock.json', 'tsconfig.json')) {
        Set-Content -LiteralPath (Join-Path $source $name) -Value '{}' -Encoding ASCII
    }
    Set-Content -LiteralPath (Join-Path $source 'src/index.ts') -Value 'new source' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $source 'bin/autoteams-runner.js') -Value 'new bin' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $source 'secret.txt') -Value 'private' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $stub 'npm.cmd') -Encoding ASCII -Value @(
        '@echo off',
        'echo %*>>"%USERPROFILE%\npm-calls.txt"',
        'if "%1"=="ci" if "%FAIL_STEP%"=="ci" exit /b 17',
        'if "%1"=="run" if "%FAIL_STEP%"=="build" exit /b 18',
        'if "%1"=="run" mkdir dist',
        'if "%1"=="run" echo new build>dist\index.js',
        'exit /b 0'
    )
    Set-Content -LiteralPath (Join-Path $stub 'node.cmd') -Encoding ASCII -Value @(
        '@echo off',
        'if "%1"=="--version" echo v20.0.0& exit /b 0',
        'echo %*>>"%USERPROFILE%\node-calls.txt"',
        'exit /b 0'
    )
    $env:PATH = "$stub;$oldPath"
    $env:USERPROFILE = $fixtureProfile
    $installed = Join-Path $fixtureProfile '.autoteams/runner'
    $legacyInstalled = Join-Path $fixtureProfile '.autofde/runner'
    New-Item -ItemType Directory -Force -Path (Join-Path $legacyInstalled 'dist') | Out-Null
    Set-Content -LiteralPath (Join-Path $legacyInstalled 'dist/index.js') -Value 'legacy build' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $legacyInstalled 'legacy-vault.json') -Value 'legacy user data' -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $legacyInstalled 'receipts') | Out-Null
    Set-Content -LiteralPath (Join-Path $legacyInstalled 'receipts/old-ledger.json') -Value 'old ledger' -Encoding ASCII
    New-Item -ItemType Directory -Force -Path (Join-Path $installed 'dist') | Out-Null
    Set-Content -LiteralPath (Join-Path $installed 'dist/index.js') -Value 'old build' -Encoding ASCII
    Set-Content -LiteralPath (Join-Path $installed 'vault.json') -Value 'user data' -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $installed 'receipts') | Out-Null
    Set-Content -LiteralPath (Join-Path $installed 'receipts/ledger.json') -Value 'ledger' -Encoding ASCII
    foreach ($failure in @('ci', 'build')) {
        $env:FAIL_STEP = $failure
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $source 'install.ps1')
        if ($LASTEXITCODE -eq 0) { throw "Expected $failure failure" }
        if ((Get-Content -Raw (Join-Path $installed 'dist/index.js')).Trim() -ne 'old build') { throw "Old build changed after $failure failure" }
        if ((Get-Content -Raw (Join-Path $installed 'vault.json')).Trim() -ne 'user data') { throw "User data changed after $failure failure" }
        if (Get-ChildItem -LiteralPath (Join-Path $fixtureProfile '.autoteams') -Name 'runner-stage-*') { throw 'Stage leaked' }
    }
    $calls = Get-Content -LiteralPath (Join-Path $fixtureProfile 'npm-calls.txt')
    if (@($calls | Where-Object { $_ -eq 'ci --include=dev' }).Count -ne 2) { throw 'npm ci not called for each attempt' }
    if (@($calls | Where-Object { $_ -eq 'run build' }).Count -ne 1) { throw 'Build not attempted despite stale dist' }
    Remove-Item Env:FAIL_STEP
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $source 'install.ps1') -SkipPathUpdate
    if ($LASTEXITCODE -ne 0) { throw 'Upgrade failed' }
    if ((Get-Content -Raw (Join-Path $installed 'dist/index.js')).Trim() -ne 'new build') { throw 'Stale dist survived upgrade' }
    if ((Get-Content -Raw (Join-Path $installed 'vault.json')).Trim() -ne 'user data') { throw 'Active vault missing after upgrade' }
    if ((Get-Content -Raw (Join-Path $installed 'receipts/ledger.json')).Trim() -ne 'ledger') { throw 'Active ledger missing after upgrade' }
    if ((Get-Content -Raw (Join-Path $legacyInstalled 'legacy-vault.json')).Trim() -ne 'legacy user data') { throw 'Legacy installation changed' }
    if (Test-Path -LiteralPath (Join-Path $installed 'secret.txt')) { throw 'Local secret copied' }
    $backups = @(Get-ChildItem -LiteralPath (Join-Path $fixtureProfile '.autoteams') -Directory -Filter 'runner-backup-*')
    if ($backups.Count -ne 1) { throw 'Previous installation backup missing' }
    if ((Get-Content -Raw (Join-Path $backups[0].FullName 'vault.json')).Trim() -ne 'user data') { throw 'User data backup missing' }
    if (-not (Test-Path -LiteralPath (Join-Path $installed 'bin/autoteams-runner.cmd'))) { throw 'Runner command wrapper missing' }
    $wrapper = Join-Path $installed 'bin/autoteams-runner.cmd'
    if ((Get-Content -Raw -LiteralPath $wrapper) -notmatch '%~dp0autoteams-runner.js') { throw 'Wrapper does not use relative path' }
    & $wrapper --help
    if ($LASTEXITCODE -ne 0) { throw 'Wrapper failed under Chinese profile' }
    if (-not @((Get-Content -LiteralPath (Join-Path $fixtureProfile 'node-calls.txt')) | Where-Object { $_ -match 'autoteams-runner.js.*--help' }).Count) { throw 'Wrapper did not invoke node' }

    # A fresh AutoTeams install migrates user files from the legacy installation,
    # while leaving its receipt directory and original installation untouched.
    Remove-Item -LiteralPath $installed -Recurse -Force
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $source 'install.ps1') -SkipPathUpdate
    if ($LASTEXITCODE -ne 0) { throw 'Legacy migration failed' }
    if ((Get-Content -Raw (Join-Path $installed 'legacy-vault.json')).Trim() -ne 'legacy user data') { throw 'Legacy user file not migrated' }
    if (Test-Path -LiteralPath (Join-Path $installed 'receipts/old-ledger.json')) { throw 'Legacy receipt ledger was copied' }
    if ((Get-Content -Raw (Join-Path $legacyInstalled 'receipts/old-ledger.json')).Trim() -ne 'old ledger') { throw 'Legacy receipt ledger changed' }

    $packageRoot = Join-Path $fixture 'package'
    $packageRunner = Join-Path $packageRoot 'local-runner'
    $packageScripts = Join-Path $packageRoot 'scripts'
    New-Item -ItemType Directory -Force -Path $packageRunner, $packageScripts | Out-Null
    Copy-Item -LiteralPath (Join-Path $repo 'scripts/package-local-runner.ps1') -Destination $packageScripts
    foreach ($item in @('package.json', 'package-lock.json', 'tsconfig.json', 'src', 'bin', 'install.ps1')) {
        Copy-Item -LiteralPath (Join-Path $source $item) -Destination $packageRunner -Recurse
    }
    Set-Content -LiteralPath (Join-Path $packageRunner 'secret.txt') -Value 'private' -Encoding ASCII
    New-Item -ItemType Directory -Path (Join-Path $packageRunner 'dist') | Out-Null
    Set-Content -LiteralPath (Join-Path $packageRunner 'dist/index.js') -Value 'stale' -Encoding ASCII
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $packageScripts 'package-local-runner.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Packaging failed' }
    $front = Join-Path $packageRoot 'frontend/public/local-runner'
    $cloud = Join-Path $packageRoot 'deploy/cloudflare/local-runner'
    $frontZip = Join-Path $front 'local-runner.zip'
    if ((Get-FileHash $frontZip).Hash -ne (Get-FileHash (Join-Path $cloud 'local-runner.zip')).Hash) { throw 'Archives differ' }
    if ((Get-FileHash (Join-Path $front 'install.ps1')).Hash -ne (Get-FileHash (Join-Path $cloud 'install.ps1')).Hash) { throw 'Installers differ' }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($frontZip)
    try {
        $names = @($archive.Entries | ForEach-Object FullName)
        if (-not @($names | Where-Object { $_ -match '^src[/\\]index.ts$' }).Count) { throw 'Source missing' }
        if ($names | Where-Object { $_ -match 'secret|dist|node_modules' }) { throw 'Excluded file packaged' }
    } finally { $archive.Dispose() }
    Write-Host 'Installer failure and distribution fixture tests passed.'
} finally {
    $env:PATH = $oldPath
    $env:USERPROFILE = $oldProfile
    Remove-Item Env:FAIL_STEP -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
