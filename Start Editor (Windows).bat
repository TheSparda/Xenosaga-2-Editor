@echo off
rem Double-click this file to launch the Xenosaga II editor on Windows.
cd /d "%~dp0Editor"
where py >nul 2>nul && ( py -3 x2editor.py & goto :eof )
where python >nul 2>nul && ( python x2editor.py & goto :eof )
echo Python 3 is required. Install from https://www.python.org/downloads/ and tick "Add to PATH".
pause
