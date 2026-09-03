@echo off
chcp 65001 >nul
cd /d "%~dp0"
title CourseMate 安装依赖

echo.
echo   正在安装 CourseMate 运行依赖...
echo.

REM 优先用 PyPI 官方源。本机 pip 若配置了有问题的镜像，
REM 全局配置会让安装静默失败（报 "from versions: none"），
REM 这里显式指定源来绕开它。
python -m pip install -r requirements.txt --index-url https://pypi.org/simple
if %errorlevel%==0 goto ok

echo.
echo   官方源失败，改用清华镜像重试...
echo.
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
if %errorlevel%==0 goto ok

echo.
echo   清华镜像失败，改用阿里云镜像重试...
echo.
python -m pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com
if %errorlevel%==0 goto ok

echo.
echo   [失败] 三个源都没装成功。
echo   请检查网络连接，或把上面的报错信息发给开发者。
echo.
pause
exit /b 1

:ok
echo.
echo   依赖安装完成。现在可以双击「启动.bat」或「CourseMate.pyw」打开软件了。
echo.
pause
