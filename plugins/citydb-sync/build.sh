#!/bin/sh
# Copyright (c) 2026 4dcitygml
# SPDX-License-Identifier: Apache-2.0
set -eu

if [ -z "${CITYDB_HOME:-}" ] || [ ! -d "$CITYDB_HOME/lib" ]; then
  echo "CITYDB_HOME must point to an unpacked citydb-tool 1.3.2 directory." >&2
  exit 2
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
TOOLS_ROOT=$(CDPATH= cd -- "$ROOT/../.." && pwd)
BUILD="$ROOT/build"
CLASSES="$BUILD/classes"
DIST="$BUILD/distributions/4dcitygml-citydb-sync"

mkdir -p "$CLASSES" "$DIST/lib" "$DIST/connector/web" \
  "$DIST/connector/xslt" "$DIST/connector/runtime/scripts"
find "$CLASSES" -type f -delete
find "$DIST" -mindepth 1 -delete
mkdir -p "$DIST/lib" "$DIST/connector/web" \
  "$DIST/connector/xslt" "$DIST/connector/runtime/scripts"

javac --release 17 \
  -classpath "$CITYDB_HOME/lib/*" \
  -d "$CLASSES" \
  $(find "$ROOT/src/main/java" -name '*.java' -type f | sort)

cp -R "$ROOT/src/main/resources/." "$CLASSES/"
jar --create --file "$DIST/lib/4dcitygml-citydb-sync-0.1.0.jar" -C "$CLASSES" .
cp "$TOOLS_ROOT/connectors/3dcitydb/connector.py" "$DIST/connector/"
cp "$TOOLS_ROOT/connectors/3dcitydb/generic_to_uro.py" "$DIST/connector/"
cp "$TOOLS_ROOT/connectors/3dcitydb/server.py" "$DIST/connector/"
cp "$TOOLS_ROOT/connectors/3dcitydb/config.example.json" "$DIST/connector/"
cp "$TOOLS_ROOT/connectors/3dcitydb/xslt/uro_to_generic.xsl" "$DIST/connector/xslt/"
cp "$TOOLS_ROOT/connectors/3dcitydb/web/index.html" "$DIST/connector/web/"
cp "$TOOLS_ROOT/connectors/3dcitydb/web/app.js" "$DIST/connector/web/"
cp "$TOOLS_ROOT/connectors/3dcitydb/web/styles.css" "$DIST/connector/web/"
cp "$TOOLS_ROOT/scripts/__init__.py" "$DIST/connector/runtime/scripts/"
cp "$TOOLS_ROOT/scripts/diff_citygml.py" "$DIST/connector/runtime/scripts/"
cp "$TOOLS_ROOT/scripts/safe_xml.py" "$DIST/connector/runtime/scripts/"
cp "$TOOLS_ROOT/scripts/reconstruct_minimal.py" "$DIST/connector/runtime/scripts/"
cp "$TOOLS_ROOT/connectors/3dcitydb/requirements.txt" "$DIST/connector/requirements.txt"
cp "$TOOLS_ROOT/LICENSE" "$DIST/"
cp "$TOOLS_ROOT/NOTICE" "$DIST/"

echo "$DIST"
