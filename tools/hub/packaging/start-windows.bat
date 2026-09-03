@echo off
rem Copyright (c) 2026 4dcitygml
rem SPDX-License-Identifier: Apache-2.0
rem citygml-hub (.py) launcher — Windows
rem In the zip this file sits at the top of the distribution folder as
rem "start-windows.bat"; everything else lives in the adjacent "program/"
rem (the intent: users only ever see the guide HTML and this file).
rem The bundled PythonPortable (python.org embeddable package) is preferred;
rem otherwise fall back to a local py / python.
rem Note: do not use pythonw.exe with PythonPortable (sys.stdout becomes None
rem       and the app's startup log prints fail). The console window stays
rem       open by design (same guidance as the last screen of READ-ME-FIRST.html).
rem (git needs no setup here: the app picks a configured existing Git first,
rem  then the bundled PortableGit.)
setlocal
set "HERE=%~dp0"

rem App location: distribution layout (program/) first, then repo-flat (development).
if exist "%HERE%program\hub.py" (
  set "LIB=%HERE%program\"
) else (
  set "LIB=%HERE%"
)

rem Prefer the bundled Python (runs with no install)
if exist "%LIB%PythonPortable\python.exe" (
  start "citygml-hub" "%LIB%PythonPortable\python.exe" "%LIB%hub.py" %*
  goto :eof
)

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"

if not defined PY (
  echo Python was not found.
  echo Use the distribution zip ^(PythonPortable bundled, no install needed^),
  echo or install Python 3.9 or newer from python.org.
  pause
  goto :eof
)

rem Version check: Python 3.9+ required (stdlib only; no dependence on a specific version).
%PY% -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,9) else 1)" 2>nul
if errorlevel 1 (
  echo Python 3.9 or newer is required. Use the distribution zip
  echo ^(PythonPortable bundled^), or install a newer Python from python.org.
  pause
  goto :eof
)

%PY% "%LIB%hub.py" %*
