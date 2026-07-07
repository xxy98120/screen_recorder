@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "APP_NAME=小开心录屏"

echo ========================================
echo  正在打包为 EXE...
echo ========================================
echo.
python -m PyInstaller --onefile --windowed --name "%APP_NAME%" --icon "icon.ico" --add-data "icon.ico;." --hidden-import sounddevice --hidden-import pyaudiowpatch --clean main.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上面的错误信息。
    pause
    exit /b 1
)

if exist "dist\%APP_NAME%.exe" (
    call :copy_ffmpeg
    echo.
    echo ========================================
    echo  打包完成！
    echo  输出文件: dist\%APP_NAME%.exe
    echo ========================================
) else (
    echo.
    echo [错误] PyInstaller 未报错，但没有找到输出文件。
    pause
    exit /b 1
)

pause

exit /b 0

:copy_ffmpeg
set "FFMPEG_EXE="
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
    if not defined FFMPEG_EXE set "FFMPEG_EXE=%%F"
)
if defined FFMPEG_EXE (
    copy /y "%FFMPEG_EXE%" "dist\ffmpeg.exe" >nul
    echo 已复制 ffmpeg: dist\ffmpeg.exe
) else (
    echo [提示] 未找到 ffmpeg，发给别人时请把 ffmpeg.exe 放到 dist 目录。
)
exit /b 0
