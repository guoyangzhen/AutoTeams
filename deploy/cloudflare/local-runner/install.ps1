# AutoTeams Local Runner installer
param([switch]$SkipPathUpdate)
$ErrorActionPreference = 'Stop'
Write-Host '正在安装 AutoTeams 本地守护进程…' -ForegroundColor Cyan
function Invoke-RunnerNpm {
    param([string[]]$Arguments)
    & npm.cmd @Arguments
    if ($LASTEXITCODE -ne 0) { throw "npm $($Arguments -join ' ') failed with exit code $LASTEXITCODE" }
}
function Copy-UserEntry {
    param([System.IO.FileSystemInfo]$Entry, [string]$Destination)
    if ($Entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Write-Host "跳过链接路径：$($Entry.FullName)" -ForegroundColor Yellow
        return
    }
    if ($Entry.PSIsContainer) {
        New-Item -ItemType Directory -Path $Destination | Out-Null
        foreach ($child in Get-ChildItem -LiteralPath $Entry.FullName -Force) {
            Copy-UserEntry -Entry $child -Destination (Join-Path $Destination $child.Name)
        }
    } else {
        Copy-Item -LiteralPath $Entry.FullName -Destination $Destination
    }
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js >=18 is required' }
$version = & node --version
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^v(\d+)\.') { throw 'Unable to read Node.js version' }
if ([int]$Matches[1] -lt 18) { throw 'Node.js >=18 is required' }
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'npm.cmd was not found' }

$installDir = Join-Path $env:USERPROFILE '.autoteams\runner'
$legacyInstallDir = Join-Path $env:USERPROFILE '.autofde\runner'
$parent = Split-Path -Parent $installDir
$stage = Join-Path $parent ("runner-stage-" + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $parent ("runner-backup-" + [guid]::NewGuid().ToString('N'))
$sourceDir = $PSScriptRoot
$base = $env:AUTOTEAMS_RUNNER_DOWNLOAD_BASE
if (-not $base) { $base = $env:AUTOFDE_RUNNER_DOWNLOAD_BASE }
if (-not $base) { $base = '__AUTOTEAMS_DOMAIN__/local-runner' }
$items = @('package.json', 'package-lock.json', 'tsconfig.json', 'src', 'bin')
$zip = $null
$oldMoved = $false
$stageMoved = $false

function Remove-RunnerStage {
    param([string]$Path)
    $full = [IO.Path]::GetFullPath($Path)
    $root = [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE '.autoteams'))
    if (-not $full.StartsWith(($root.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase) -or
        -not ([IO.Path]::GetFileName($full) -like 'runner-stage-*')) { throw "Unsafe cleanup path: $full" }
    if (-not (Test-Path -LiteralPath $full)) { return }
    $entry = Get-Item -LiteralPath $full -Force
    if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        [IO.Directory]::Delete($full, $false) # Remove the junction itself, never traverse its target.
    } else {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

# Moves and recursive cleanup stay inside this user's runner directory.
$safeParent = [IO.Path]::GetFullPath($parent).TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
foreach ($candidate in @($installDir, $stage, $backup)) {
    $full = [IO.Path]::GetFullPath($candidate)
    if (-not $full.StartsWith($safeParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe runner path: $full"
    }
}

try {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    New-Item -ItemType Directory -Path $stage | Out-Null
    if (-not $sourceDir -or -not (Test-Path -LiteralPath (Join-Path $sourceDir 'package.json'))) {
        if ($base -like '__AUTOTEAMS_DOMAIN__*') {
            throw '请设置 AUTOTEAMS_RUNNER_DOWNLOAD_BASE 为已部署站点的 /local-runner 地址，再运行独立安装脚本'
        }
        $zip = Join-Path $env:TEMP ("autoteams-runner-" + [guid]::NewGuid().ToString('N') + '.zip')
        Invoke-WebRequest -Uri "$($base.TrimEnd('/'))/local-runner.zip" -OutFile $zip -UseBasicParsing
        Expand-Archive -LiteralPath $zip -DestinationPath $stage
        $sourceDir = $stage
    }
    foreach ($item in $items) {
        $source = Join-Path $sourceDir $item
        if (-not (Test-Path -LiteralPath $source)) { throw "Runner source is missing $item" }
        if ($sourceDir -ne $stage) { Copy-Item -LiteralPath $source -Destination $stage -Recurse }
    }
    Push-Location $stage
    try {
        Write-Host '正在安装依赖…' -ForegroundColor Yellow
        Invoke-RunnerNpm -Arguments @('ci', '--include=dev')
        Write-Host '正在编译最新源码…' -ForegroundColor Yellow
        Invoke-RunnerNpm -Arguments @('run', 'build')
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath (Join-Path $stage 'dist\index.js') -PathType Leaf)) {
        throw 'Build completed without dist/index.js'
    }
    # Keep files the user or runner created next to the distribution at their active paths.
    # Source, dependencies, build output, command wrapper and package metadata come from this release.
    $preserveFrom = if (Test-Path -LiteralPath $installDir) { $installDir } elseif (Test-Path -LiteralPath $legacyInstallDir) { $legacyInstallDir } else { $null }
    if ($preserveFrom) {
        $oldRoot = Get-Item -LiteralPath $preserveFrom -Force
        if ($oldRoot.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Existing runner path is a link: $preserveFrom"
        }
        $distribution = @('src', 'bin', 'dist', 'node_modules', 'package.json',
            'package-lock.json', 'tsconfig.json', 'install.ps1')
        foreach ($entry in Get-ChildItem -LiteralPath $preserveFrom -Force) {
            if ($distribution -notcontains $entry.Name) {
                if ($preserveFrom -eq $legacyInstallDir -and $entry.Name -eq 'receipts') {
                    Write-Host "旧回执目录保持原位：$($entry.FullName)" -ForegroundColor Yellow
                    continue
                }
                Copy-UserEntry -Entry $entry -Destination (Join-Path $stage $entry.Name)
            }
        }
    }
    if (Test-Path -LiteralPath $installDir) {
        Move-Item -LiteralPath $installDir -Destination $backup
        $oldMoved = $true
    }
    Move-Item -LiteralPath $stage -Destination $installDir
    $stageMoved = $true
    $binDir = Join-Path $installDir 'bin'
    $cmd = Join-Path $binDir 'autoteams-runner.cmd'
    "@echo off`r`nnode `"%~dp0autoteams-runner.js`" %*" | Set-Content -LiteralPath $cmd -Encoding ASCII
    if (-not $SkipPathUpdate) {
        $currentPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        if (($currentPath -split ';') -notcontains $binDir) {
            [Environment]::SetEnvironmentVariable('Path', "$currentPath;$binDir", 'User')
        }
    }
    if ($oldMoved) { Write-Host "旧版本已保留在：$backup" }
    Write-Host "AutoTeams 本地守护进程安装完成：$installDir" -ForegroundColor Green
    Write-Host '下一步：在 AutoTeams 网页「协作工作台 → 本地连接」中注册本地路径，复制生成的连接命令并运行。'
    Write-Host '新开 PowerShell 后，可运行 autoteams-runner --help 查看帮助。'
} catch {
    if ($stageMoved -and (Test-Path -LiteralPath $installDir)) {
        Move-Item -LiteralPath $installDir -Destination $stage
    }
    if ($oldMoved -and (Test-Path -LiteralPath $backup)) {
        Move-Item -LiteralPath $backup -Destination $installDir
    }
    Write-Error "本地守护进程安装失败：$_"
    exit 1
} finally {
    Remove-RunnerStage -Path $stage
    if ($zip -and (Test-Path -LiteralPath $zip)) { Remove-Item -LiteralPath $zip -Force }
}
