#!/usr/bin/env bash
# Regenerate gRPC stubs from ../daemon/protos/meshcored.proto into meshcli/pb.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-.venv/bin/python}"
SRC="../daemon/protos/meshcored.proto"
OUT=meshcli/pb

mkdir -p "$OUT"
"$PY" -m grpc_tools.protoc \
    -I"$(dirname "$SRC")" \
    --python_out="$OUT" \
    --grpc_python_out="$OUT" \
    --pyi_out="$OUT" \
    "$(basename "$SRC")"

# Make the generated import package-relative.
sed -i.bak 's/^import meshcored_pb2 as/from . import meshcored_pb2 as/' "$OUT/meshcored_pb2_grpc.py"
rm -f "$OUT/meshcored_pb2_grpc.py.bak"

# Touch __init__.py so the pb package is importable.
touch "$OUT/__init__.py"

echo "generated stubs in $OUT"
