#!/bin/bash
# Incremental rebuild of just the binary the web app needs.
#
# The Python layer (app.py + deadinternet/) talks to exactly one binary: it
# launches build/tts-server and then speaks HTTP to it. It never invokes
# qwen-tts, qwen-codec or quantize -- those are the upstream CLI tools, still
# built by the build*.sh scripts, still shipped in the Docker images, just not
# on this app's path.
#
# Configure once with the script for your backend -- buildvulkan.sh, buildcuda.sh,
# buildcpu.sh -- which wipes build/ and runs cmake. After that use this for every
# rebuild; it skips compiling three tools/*.cpp files and linking four binaries.
#
# The saving is modest: the expensive part of any build here is the ggml backend
# kernels, which every target shares. The point is that "the app needs one
# binary" is written down and enacted, at no cost to merging upstream.
set -e

if [ ! -d build ]; then
    echo "buildapp.sh: no build/ directory -- configure first with one of:" >&2
    echo "    ./buildvulkan.sh   ./buildcuda.sh   ./buildcpu.sh" >&2
    exit 1
fi

cmake --build build --config Release --target tts-server -j "$(nproc)"
