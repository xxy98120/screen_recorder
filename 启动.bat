@echo off
chcp 65001 >nul
title 小开心录屏 - 安装与启动

echo ========================================
echo  桌面屏幕录制工具 - 安装与启动
echo ========================================
echo.
echo [提示] 使用清华镜像源加速下载...
echo.

echo [1/2] 正在安装依赖包（首次运行需1-2分钟）...
pip install PyQt5 opencv-python mss numpy Pillow sounddevice pyaudiowpatch -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [尝试] 切换到阿里云镜像源...
    pip install PyQt5 opencv-python mss numpy Pillow sounddevice pyaudiowpatch -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
)

IF %ERRORLEVEL% NEQ 0 (
    echo.
    echo [错误] 依赖安装失败，请检查网络连接后重试。
    pause
    exit /b 1
)

echo.
echo [2/2] 正在启动录制软件...
echo.
python main.py

pause
