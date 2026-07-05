
# if uv is not installed
# sudo dnf/apt install uv  according to linux distro
# brew install uv for macos

uv sync

# For linux
uv run python -m nuitka \
    --onefile \
    --enable-plugin=pydantic \
    --enable-plugin=anti-bloat \
    --include-package-data=ortools \
    --assume-yes-for-downloads \
    --output-dir=dist/linux \
    --output-filename=timetable \
    --lto=yes \
    --strip \
    --onefile-tempdir-spec="%CACHE_DIR%/timetable_app" \
    src/main.py


# For windows

#         uv run python -m nuitka `
#            --onefile `
#            --enable-plugin=pydantic `
#            --enable-plugin=anti-bloat `
#            --include-package-data=ortools `
#            --assume-yes-for-downloads `
#            --output-dir=dist\windows `
#            --output-filename=timetable.exe `
#            --lto=yes `
#            --strip `
#            --remove-output `
#            --noinclude-pytest-mode=nofollow `
#            --noinclude-setuptools-mode=nofollow `
#            --onefile-tempdir-spec="%CACHE_DIR%\timetable_app" `
#            src/main.py



# For macos

#        uv run python -m nuitka \
#            --onefile \
#            --follow-imports \
#            --include-package=ortools \
#            --include-package-data=ortools \
#            --include-module=google.protobuf \
#            --include-package=pydantic \
#            --include-package=pydantic_core \
#            --include-package=typer \
#            --include-package=rich \
#            --assume-yes-for-downloads \
#            --output-dir=dist/macos \
#            --output-filename=timetable \
#            --lto=yes \
#            --clang \
#            src/main.py  