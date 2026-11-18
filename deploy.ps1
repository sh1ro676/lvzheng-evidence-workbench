# 律途一键部署：构建 → 上传前端 → 服务器拉代码 → 重启后端
# 用法：在项目根目录执行 .\deploy.ps1
param(
    [string]$Server = "47.112.141.157",
    [string]$User = "admin",
    [string]$Key = "$env:USERPROFILE\.ssh\lvtu_deploy",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

if (-not $SkipBuild) {
    Write-Host "== 1/4 前端测试 ==" -ForegroundColor Cyan
    npm run test:frontend
    Write-Host "== 2/4 构建 ==" -ForegroundColor Cyan
    npm run build
}

Write-Host "== 3/4 上传 dist ==" -ForegroundColor Cyan
scp -i $Key -o BatchMode=yes -r dist "${User}@${Server}:/opt/lvtu/"

Write-Host "== 4/4 后端更新并重启 ==" -ForegroundColor Cyan
ssh -i $Key -o BatchMode=yes "${User}@${Server}" "cd /opt/lvtu && git pull --ff-only && sudo systemctl restart lvtu && sleep 2 && curl -s -o /dev/null -w 'health %{http_code}\n' http://127.0.0.1:8787/api/health"

Write-Host "== 完成：http://${Server}:8080 ==" -ForegroundColor Green
