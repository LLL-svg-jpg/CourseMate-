@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CourseMate 刷课助手

rem 优先用打包好的 exe。用 pythonw 跑 .pyw 也能用，但那样任务管理器里
rem 显示的是 Python 和 Python 的图标，看不出是哪个软件。
if exist "dist\CourseMate\CourseMate.exe" (
    start "" "dist\CourseMate\CourseMate.exe"
    exit /b 0
)

echo   没有找到打包好的 exe，改用 Python 直接运行。
echo   （这样任务管理器里会显示成 Python；想显示成本软件，请先执行 python build.py）
echo.

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "CourseMate.pyw"
    exit /b 0
)

where python >nul 2>nul
if %errorlevel%==0 (
    start "" python "CourseMate.pyw"
    exit /b 0
)

echo.
echo   [错误] 没有找到 Python。
echo.
echo   请先安装 Python 3.11 或更高版本：
echo   https://www.python.org/downloads/
echo.
echo   安装时务必勾选 "Add Python to PATH"。
echo.
pause
