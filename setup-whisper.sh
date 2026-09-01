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

# Vulkan, and both binaries. The measurement that decided this is subtle:
# spawning whisper-cli per clip, a Vulkan build and a CPU build are identical
# (0.32s each) because process startup and model load swamp the arithmetic --
# whisper's own timings are load 96ms, encode 38ms, decode 8ms. Remove that
# overhead with a resident server and the compute gap appears:
#
#     spawn whisper-cli per clip   640 ms
#     resident, CPU, 16 threads    619 ms
#     resident, Vulkan             92 ms
#
# So residency is most of the win and the GPU is the rest, but only once
# residency exists. Costs ~0.45 GB of VRAM held for the life of the process.
# On a card that tts-server has already allocated on, start it *after*
# tts-server -- evicted TTS buffers never recover (see DEADINTERNET.md).
cmake -S "$DIR" -B "$DIR/build" -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_TESTS=OFF \
      -DWHISPER_BUILD_EXAMPLES=ON -DGGML_VULKAN=ON
cmake --build "$DIR/build" --config Release -j "$(nproc)" --target whisper-cli whisper-server

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
