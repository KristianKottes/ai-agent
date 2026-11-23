#!/bin/sh
set -eu
cd "$(dirname "$0")/../../.."

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
  echo "The experimental portrait runtime targets Apple Silicon Macs." >&2
  exit 1
fi

backend/.venv/bin/python -m pip install --cache-dir backend/.pip-cache --only-binary=:all: uv==0.11.2
export UV_CACHE_DIR="$PWD/backend/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$PWD/backend/.managed-python"
uv=backend/.venv/bin/uv
python=backend/.portrait-venv/bin/python

"$uv" venv --allow-existing --python 3.12.12 backend/.portrait-venv
rm -f backend/.portrait-venv/.ready-v1
"$uv" pip sync --python "$python" --require-hashes backend/avatar/portrait_runtime/requirements.txt

scratch="$(mktemp -d)"
trap 'rm -f "$scratch/musetalk-mlx.tar.gz"; rmdir "$scratch"' EXIT
curl --fail --location --silent --show-error \
  https://codeload.github.com/xocialize/musetalk-mlx/tar.gz/c6eb30ebd1ed4d043983209813370153de9346bf \
  --output "$scratch/musetalk-mlx.tar.gz"
echo "5dc8ed647079957a10d6c8079122bc7be32e84516d7348500eff96a5e91aab67  $scratch/musetalk-mlx.tar.gz" | shasum -a 256 -c -
"$uv" pip install --python "$python" --no-deps "$scratch/musetalk-mlx.tar.gz"
"$python" -B -m backend.avatar.portrait_runtime.worker --probe
touch backend/.portrait-venv/.ready-v1
