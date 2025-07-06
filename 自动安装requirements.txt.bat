@echo off
:: ��װrequirements.txt�е�Python������
:: �Զ���ȡ��ǰPython������·����ִ��pip��װ

echo ���ڰ�װPython������...

:: ��ȡ��ǰPython������·��
for /f "delims=" %%i in ('where python') do (
    set "PYTHON_EXE=%%i"
    goto :found_python
)

:: ���where pythonû�ҵ�������where python3
if not defined PYTHON_EXE (
    for /f "delims=" %%i in ('where python3') do (
        set "PYTHON_EXE=%%i"
        goto :found_python
    )
)

:: �����û�ҵ��������˳�
if not defined PYTHON_EXE (
    echo ����: δ�ҵ�Python����������ȷ��Python�Ѱ�װ����ӵ�ϵͳPATH���������С�
    pause
    exit /b 1
)

:found_python
echo ʹ��Python������: %PYTHON_EXE%

:: ���pip�Ƿ����
%PYTHON_EXE% -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ����: �Ҳ���pip����ȷ��pip�Ѱ�װ��
    pause
    exit /b 1
)

:: ���requirements.txt�Ƿ����
if not exist "requirements.txt" (
    echo ����: ��ǰĿ¼���Ҳ���requirements.txt�ļ���
    pause
    exit /b 1
)

:: ʹ�õ�ǰPython��������pip��װ����
echo ����ͨ�� %PYTHON_EXE% ��װ����...
%PYTHON_EXE% -m pip install -r requirements.txt

if %errorlevel% equ 0 (
    echo ������װ�ɹ�!
) else (
    echo ����: ������װ�����г������⡣
)

pause