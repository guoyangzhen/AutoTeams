<#
# ============================================================
# AutoTeams 增量同步部署脚本（本地 Windows 使用）
#
# 用途：
#   本地修改代码后，仅重建/推送受影响的服务镜像到 GHCR，
#   再通知云服务器 docker compose pull + up -d 拉取并重启变更容器。
#
#   由于 Docker 镜像按层缓存，只会上传/下载真正变化的部分，
#   天然实现"仅推送修改内容"的增量同步。
#
# 用法：
#   # 只更新某个服务
#   powershell -ExecutionPolicy Bypass -File deploy\sync-deploy.ps1 -Target backend
#   powershell -ExecutionPolicy Bypass -File deploy\sync-deploy.ps1 -Target frontend
#   powershell -ExecutionPolicy Bypass -File deploy\sync-deploy.ps1 -Target collab
#   powershell -ExecutionPolicy Bypass -File deploy\sync-deploy.ps1 -Target backup
#   # 更新全部
#   powershell -ExecutionPolicy Bypass -File deploy\sync-deploy.ps1 -Target all
#
# 可选参数（一般用默认即可）：
#   -SshKey <私钥路径>      默认 C:\path\to\your-key.pem
#   -SshHost <IP>           默认 YOUR_SERVER_IP
#   -SshUser <用户>         默认 ubuntu
#   -GhOwner <GH 用户名>    默认 guoyangzhen
#   -GhTokenFile <PAT>      默认 D:\Working\github_tokenGHCR.txt
#   -GhcrMirrorDeps        后端构建使用国内 APT/PyPI 镜像（默认真，国内网络推荐）
# ============================================================
#>
param(
    [ValidateSet("backend","frontend","collab","backup","all")]
    [string]$Target = "all",
    [string]$SshKey = "C:\path\to\your-key.pem",
    [string]$SshHost = "YOUR_SERVER_IP",
    [string]$SshUser = "ubuntu",
    [string]$GhOwner = "guoyangzhen",
    [string]$GhTokenFile = "D:\Working\github_tokenGHCR.txt",
    [switch]$GhcrMirrorDeps = $true
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# ---------- 工具函数 ----------
function Write-Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)    { Write-Host "   [OK] $msg" -ForegroundColor Green }

# 登录 GHCR（幂等）
function Ensure-GhcrLogin {
    if (-not (Test-Path $GhTokenFile)) { throw "未找到 GHCR token 文件: $GhTokenFile" }
    $tok = (Get-Content $GhTokenFile -Raw).Trim()
    Write-Step "登录 GHCR (ghcr.io/$GhOwner)"
    Write-Output $tok | docker login ghcr.io -u $GhOwner --password-stdin | Out-Null
    Write-Ok "GHCR 登录成功"
}

# 后端精简构建参数
function Get-BackendArgs {
    $args = @("--build-arg","SKIP_MODEL_DOWNLOAD=true","--build-arg","REQUIREMENTS_FILE=requirements-slim.txt")
    if ($GhcrMirrorDeps) {
        $args += @("--build-arg","APT_MIRROR=http://mirrors.aliyun.com/debian","--build-arg","PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/")
    }
    return $args
}

function Build-And-Push($name, $tag, $context, $dockerfile, $buildArgs) {
    Write-Step "构建镜像 $name"
    $img = "ghcr.io/$GhOwner/$name:latest"
    $cmd = @("docker","build","-t",$img)
    if ($dockerfile) { $cmd += @("-f",$dockerfile) }
    if ($buildArgs)  { $cmd += $buildArgs }
    $cmd += $context
    & $cmd
    if ($LASTEXITCODE -ne 0) { throw "构建 $name 失败" }
    Write-Ok "构建完成: $img"

    Write-Step "推送镜像 $img"
    & docker push $img
    if ($LASTEXITCODE -ne 0) { throw "推送 $name 失败" }
    Write-Ok "推送完成: $img"
}

# ---------- 服务端更新 ----------
function Update-Server {
    Write-Step "通知云服务器拉取并重启服务"
    $script = @'
cd ~/autoteams-deploy
sudo docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml --env-file .env.prod pull
sudo docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml --env-file .env.prod up -d
sudo docker compose -f docker-compose.prod.yml -f docker-compose.prod.override.yml --env-file .env.prod ps
'@
    & ssh -i $SshKey -o StrictHostKeyChecking=no ${SshUser}@${SshHost} $script
    if ($LASTEXITCODE -ne 0) { throw "服务器更新失败" }
    Write-Ok "服务器已更新并重启服务"
}

# ---------- 主流程 ----------
Ensure-GhcrLogin

$targets = if ($Target -eq "all") { @("backend","frontend","collab","backup") } else { @($Target) }

foreach ($t in $targets) {
    switch ($t) {
        "backend" { Build-And-Push "autoteams-backend" $null "backend" $null (Get-BackendArgs) }
        "frontend" { Build-And-Push "autoteams-frontend" $null "frontend" "frontend/Dockerfile" @() }
        "collab"  { Build-And-Push "autoteams-collab" $null "collaboration-service" "collaboration-service/Dockerfile" @() }
        "backup"  { Build-And-Push "autoteams-backup" $null "." "docker/backup/Dockerfile" @() }
    }
}

Update-Server
Write-Host "`n全部完成！" -ForegroundColor Green
