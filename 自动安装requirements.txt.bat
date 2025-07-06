@echo off
:: 安装requirements.txt中的Python依赖包
:: 自动获取当前Python解释器路径并执行pip安装

echo 正在安装Python依赖包...

:: 获取当前Python解释器路径
for /f "delims=" %%i in ('where python') do (
    set "PYTHON_EXE=%%i"
    goto :found_python
)

:: 如果where python没找到，尝试where python3
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('where python3') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)

:: 如果都没找到，报错退出
if not defined PYTHON_EXE (
    echo 错误: 未找到Python解释器。请确保Python已安装并添加到系统PATH环境变量中。
    pause
    exit /b 1
)

:found_python
echo 使用Python解释器: %PYTHON_EXE%

:: 检查pip是否可用
%PYTHON_EXE% -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误: 找不到pip。请确保pip已安装。
    pause
    exit /b 1
)

:: 检查requirements.txt是否存在
if not exist "requirements.txt" (
    echo 错误: 当前目录下找不到requirements.txt文件。
    pause
    exit /b 1
)

:: 使用当前Python解释器的pip安装依赖
echo 正在通过 %PYTHON_EXE% 安装依赖...
%PYTHON_EXE% -m pip install -r requirements.txt

if %errorlevel% equ 0 (
    echo 依赖安装成功!
) else (
    echo 错误: 依赖安装过程中出现问题。
)

pause