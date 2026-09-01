#!/bin/bash
# Build whisper.cpp for the microphone input on the Run tab.
#
# Deliberately NOT a git submodule. .gitmodules is upstream's file -- it holds
# their ggml pin -- so adding an entry there would put a merge conflict in the
# path of every upstream pull, forever. A checkout this script owns costs
# nothing to re-create and touches nothing upstream tracks.
#
# CPU only, on purpose. This machine's constraint is VRAM, not compute: the
# card is ~63% full with tts-server alone, and once the driver evicts
# tts-server's Vulkan buffers speech stays ~5x slow until it is restarted (see
# the VRAM section in DEADINTERNET.md). A 9950X transcribes a spoken line in a
# couple of seconds with the GPU untouched, which is the right trade here.
#
#   ./setup-whisper.sh              # base.en  (142 MB, fastest)
#   ./setup-whisper.sh small.en     # default  (466 MB, better on names)
#   ./setup-whisper.sh medium.en    # 1.5 GB, slower than speech is worth
set -e

MODEL="${1:-small.en}"
DIR="whisper.cpp"

if ! command -v cmake >/dev/null || ! command -v git >/dev/null; then
    echo "setup-whisper.sh: needs git and cmake on PATH" >&2
    exit 1
fi

if [ ! -d "$DIR" ]; then
    echo "==> cloning whisper.cpp"
    git clone --depth 1 https://github.com/ggml-org/whisper.cpp.git "$DIR"
fi

# CPU on purpose, and this was measured rather than assumed. whisper-cli is
# spawned per clip, so its own timings on a 2s utterance read:
#
#     load 96ms | mel 2ms | encode 38ms | decode 8ms | total 250ms
#
# Only the 38ms encode is GPU work. A Vulkan build measured 0.32s against the
# CPU build's 0.32s -- no difference, because the cost is process startup and
# model load, not arithmetic. It is also actively worse here: each spawn would
# take a Vulkan context and ~0.5GB of VRAM on a card that tts-server has
# already allocated on, and evicted TTS buffers never recover (see the VRAM
# section of DEADINTERNET.md). The win worth having is a resident whisper,
# not a faster one.
cmake -S "$DIR" -B "$DIR/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON -DGGML_VULKAN=OFF
cmake --build "$DIR/build" --config Release -j "$(nproc)" --target whisper-cli

echo "==> fetching model: $MODEL"
"$DIR/models/download-ggml-model.sh" "$MODEL"

BIN="$DIR/build/bin/whisper-cli"
MODEL_PATH="$DIR/models/ggml-$MODEL.bin"
echo
echo "done."
echo "  binary : $BIN"
echo "  model  : $MODEL_PATH"
echo
echo "If you chose a model other than the default, set whisper_model in"
echo "deadinternet.json to '$MODEL_PATH' (or use the Behaviour tab)."
