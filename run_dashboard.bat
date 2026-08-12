@echo off
chcp 65001 >nul
setlocal

set ROOT=%~dp0
set PY=%ROOT%python-embed\python.exe

if not exist "%PY%" (
    echo 找不到 %PY%
    echo 這個資料夾少了 python-embed，請確認是完整的一份，不是只複製部分檔案。
    pause
    exit /b 1
)

echo 正在讀取最新資料、重新產生儀表板內容...
echo.

set PYTHONPATH=%ROOT%code
pushd "%ROOT%code"
"%PY%" -m pipeline.build_all
set ERR=%ERRORLEVEL%
popd

if not "%ERR%"=="0" (
    echo.
    echo 發生錯誤（上面有訊息），儀表板不會開啟。
    echo 請把這個視窗的內容截圖，回報給維護者。
    pause
    exit /b 1
)

echo.
echo 完成，正在開啟儀表板...
start "" "%ROOT%code\dashboard\index.html"

endlocal
