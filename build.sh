#!/usr/bin/env bash
set -euo pipefail

# if uv is not installed
# sudo dnf/apt install uv  according to linux distro
# brew install uv for macos

uv sync

# Shared notes for all platforms:
#   --lto=yes          link-time optimization for the fastest runtime
#   --onefile-cache-mode=cached
#   --onefile-tempdir-spec
#                      unpack once into a stable cache instead of per run
#   --remove-output    delete Nuitka's build directory afterwards
#
# Nuitka 4.x strips binaries by default (there is no --strip option) and has
# no "pydantic" plugin, so pydantic/pydantic_core are included explicitly.
# The anti-bloat plugin is always enabled and does not need --enable-plugin.
#
# Onefile compression needs the "zstandard" dev dependency (see pyproject.toml);
# without it the payload is stored uncompressed and the binary is much larger.
# --include-package-data=textual is not used: the only data files Textual ships
# are tree-sitter grammars for its optional "syntax" extra, which this app
# never installs. --include-package-data=ortools is also omitted because Nuitka's
# built-in ortools package config already bundles ortools/.libs as DLLs (passing
# it on Linux conflicts with versioned .so.* files like libCbc.so.2).

# For linux
uv run python -m nuitka \
    --onefile \
    --include-package=maketable \
    --include-package=pydantic \
    --include-package=pydantic_core \
    --include-package=textual \
    --assume-yes-for-downloads \
    --output-dir=dist/linux \
    --output-filename=timetable \
    --lto=yes \
    --onefile-cache-mode=cached \
    --onefile-tempdir-spec="{CACHE_DIR}/timetable_app" \
    --remove-output \
    src/main.py


# For windows (PowerShell)
#
#        uv run python -m nuitka `
#            --onefile `
#            --include-package=maketable `
#            --include-package=pydantic `
#            --include-package=pydantic_core `
#            --include-package=textual `
#            --assume-yes-for-downloads `
#            --output-dir=dist\windows `
#            --output-filename=timetable.exe `
#            --lto=yes `
#            --mingw64 `
#            --windows-console-mode=force `
#            --onefile-cache-mode=cached `
#            --onefile-tempdir-spec="{CACHE_DIR}\timetable_app" `
#            --remove-output `
#            --noinclude-pytest-mode=nofollow `
#            --noinclude-setuptools-mode=nofollow `
#            --noinclude-unittest-mode=nofollow `
#            src/main.py


# For macos
#
# Nuitka 4.1.x downloads an x86_64-only ccache that fails under Rosetta on
# Apple Silicon, so the macOS build disables ccache.
#
#        uv run python -m nuitka \
#            --onefile \
#            --include-package=maketable \
#            --include-package=pydantic \
#            --include-package=pydantic_core \
#            --include-package=textual \
#            --output-dir=dist/macos \
#            --output-filename=timetable \
#            --lto=yes \
#            --clang \
#            --onefile-cache-mode=cached \
#            --onefile-tempdir-spec="{CACHE_DIR}/timetable_app" \
#            --disable-cache=ccache \
#            --remove-output \
#            src/main.py
