@echo off
rem Copyright (c) 2026 4dcitygml
rem SPDX-License-Identifier: Apache-2.0
rem CityGML attribute editor launcher — Windows (flat distribution layout)
rem In the zip this file sits in the same folder as app.py, PythonPortable/ and
rem PortableGit/. The bundled PythonPortable (python.org embeddable package) is
rem preferred; otherwise fall back to a local py / python.
rem Note: do not use pythonw.exe (sys.stdout becomes None and startup log prints fail).
setlocal
set "HERE=%~dp0"

rem Prefer the bundled Python (runs with no install)
if exist "%HERE%PythonPortable\python.exe" (
  start "Attribute Editor" "%HERE%PythonPortable\python.exe" "%HERE%app.py" %*
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

%PY% "%HERE%app.py" %*
