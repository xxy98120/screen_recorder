@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "APP_NAME=小开心录屏"

echo ========================================
echo  重新打包 %APP_NAME%
echo ========================================
echo.
echo [1/3] 清理旧文件...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo.
echo [2/3] 开始打包...
call :find_ffmpeg
if not defined FFMPEG_EXE (
    echo [错误] 未找到 ffmpeg，无法打包单文件内置版。
    echo 请先安装 ffmpeg 并加入 PATH。
    pause
    exit /b 1
)
python -m PyInstaller --onefile --windowed --name "%APP_NAME%" --icon "icon.ico" --add-data "icon.ico;." --add-binary "%FFMPEG_EXE%;." --hidden-import sounddevice --hidden-import pyaudiowpatch --clean main.py
if errorlevel 1 (
    echo.
    echo [错误] 打包失败，请查看上面的错误信息。
    pause
    exit /b 1
)

echo.
if exist "dist\%APP_NAME%.exe" (
    echo [3/3] 打包成功！
    echo 输出: dist\%APP_NAME%.exe
) else (
    echo [错误] PyInstaller 未报错，但没有找到输出文件。
    pause
    exit /b 1
)

pause

exit /b 0

:find_ffmpeg
set "FFMPEG_EXE="
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
    if not defined FFMPEG_EXE set "FFMPEG_EXE=%%F"
)
exit /b 0
