@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动律途法律 AI 助手，允许同一 Wi-Fi 下的手机访问。
echo 手机请打开：http://192.168.1.9:7100
echo 如 Windows 弹出防火墙提示，请允许“专用网络”访问。
echo 关闭本窗口或按 Ctrl+C 即可停止服务。
npm run dev -- --host 0.0.0.0 --port 7100
pause
