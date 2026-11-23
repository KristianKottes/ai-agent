#!/bin/sh
set -eu

desktop_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
workspace_dir=$(CDPATH='' cd -- "$desktop_dir/../.." && pwd)
stage="$desktop_dir/src-tauri/.macos-build/zonos-native"
revision=96d9e0ec1e39083ed336ac78f24e3dbc93721c0f
source_dir="$stage/source"
build_dir="$stage/build"

if [ "$(uname -s)-$(uname -m)" != Darwin-arm64 ]; then
    echo "Build the ZONOS3-Mini runtime on an Apple Silicon Mac." >&2
    exit 1
fi
if ! command -v cargo >/dev/null && [ -x "$workspace_dir/.cargo/bin/cargo" ]; then
    export PATH="$workspace_dir/.cargo/bin:$PATH"
    export CARGO_HOME="$workspace_dir/.cargo" RUSTUP_HOME="$workspace_dir/.rustup"
fi
mkdir -p "$source_dir" "$stage/bin" "$stage/licenses/textnorm"
if [ ! -d "$source_dir/.git" ]; then
    git -C "$source_dir" init -q
    git -C "$source_dir" remote add origin https://github.com/Zyphra/ZONOS3-Mini.git
fi
if ! git -C "$source_dir" cat-file -e "$revision^{commit}" 2>/dev/null; then
    git -C "$source_dir" fetch --depth 1 origin "$revision"
fi
git -C "$source_dir" checkout --detach "$revision"
git -C "$source_dir" submodule update --init --depth 1 servers/ggml/ggml third_party/zyphra-textnorm
cmake -S "$source_dir/servers/ggml" -B "$build_dir" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_OSX_DEPLOYMENT_TARGET=14.0 -DBUILD_SHARED_LIBS=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON
cmake --build "$build_dir" --target zonos3-cli spk-encoder-cli --parallel 6
install -m 755 "$build_dir/zonos3-cli" "$build_dir/spk-encoder-cli" "$stage/bin"
cp "$source_dir/LICENSE" "$stage/licenses/ZONOS3-LICENSE"
cp "$source_dir/NOTICE" "$stage/licenses/ZONOS3-NOTICE"
cp "$source_dir/servers/ggml/ggml/LICENSE" "$stage/licenses/ggml-LICENSE"
cp -R "$source_dir/third_party/licenses/." "$stage/licenses"
cp -R "$source_dir/third_party/zyphra-textnorm/licenses/." "$stage/licenses/textnorm"
cp "$source_dir/third_party/zyphra-textnorm/NOTICE" "$stage/licenses/textnorm/NOTICE"
