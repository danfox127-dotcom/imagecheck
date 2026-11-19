#!/bin/sh
# Install or update the image usage checker (macOS on Apple silicon, or Linux).
#
#   curl -LsSf https://raw.githubusercontent.com/danfox127-dotcom/imagecheck/main/install.sh | sh
#
# Installs into ~/imagecheck (override with IMAGECHECK_DIR), puts an "Image checker" launcher on
# the Desktop, downloads the latest shared site data, and starts the app. Running it again updates
# the app and the data; your own scans and downloaded images are kept. Nothing needs admin rights:
# Python and the libraries go into ~/imagecheck and uv's cache.
set -eu

REPO="danfox127-dotcom/imagecheck"
BRANCH="${IMAGECHECK_BRANCH:-main}"
DIR="${IMAGECHECK_DIR:-$HOME/imagecheck}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
fail() { printf '\n\033[31mSetup stopped: %s\033[0m\n' "$1" >&2; exit 1; }

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64 | Linux-x86_64 | Linux-aarch64) ;;
    Darwin-x86_64) fail "this Mac has an Intel processor; the image model's library (PyTorch) no longer supports Intel Macs." ;;
    *) fail "unsupported system $(uname -s) $(uname -m)." ;;
esac

# 1. uv: installs Python and the libraries without touching the system Python.
if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV="$HOME/.local/bin/uv"
else
    say "Installing uv (manages Python for the app)"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh
    UV="$HOME/.local/bin/uv"
fi

# 2. The app. Unpacking over an existing install updates the code and leaves data/ alone
#    (scans, the index and downloaded images aren't part of the download).
say "Downloading the app"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz" | tar -xz -C "$TMP"
SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
[ -f "$SRC/app.py" ] || fail "the download didn't contain the app."
mkdir -p "$DIR"
cp -R "$SRC/." "$DIR/"

# 3. Python and libraries (about 1.3 GB the first time; a few seconds on updates).
say "Installing Python and libraries (the first time takes a few minutes)"
cd "$DIR"
"$UV" venv --quiet --allow-existing --python 3.11 .venv
if [ "$(uname -s)" = "Linux" ]; then
    # The default Linux build of PyTorch bundles GPU libraries the app doesn't use.
    "$UV" pip install --quiet --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu
fi
# Precompiling makes the first launch quick instead of a minute-long blank page.
"$UV" pip install --quiet --compile-bytecode --python .venv/bin/python -r requirements.txt

# Load every library once and download the image model (~90 MB) now, while waiting is expected:
# macOS checks each newly installed library the first time it loads, which otherwise makes the
# first launch sit on a blank page for a minute or more.
say "Preparing the image model (one time, about a minute)"
.venv/bin/python -c "import pandas, pyarrow, streamlit; from imagecheck import scan, search; from imagecheck.features import Embedder; Embedder()" >/dev/null 2>&1 \
    || echo "(Couldn't finish this now; it will happen the first time you check an image.)"

# 4. Site data, if the published copy is newer than what's here.
say "Getting the latest site data"
.venv/bin/python scripts/get_data.py || true

# 5. A launcher on the Desktop (Linux: in ~/imagecheck only).
chmod +x scripts/start.sh
if [ "$(uname -s)" = "Darwin" ] && [ -d "$HOME/Desktop" ] && [ -z "${IMAGECHECK_NO_SHORTCUT:-}" ]; then
    LAUNCHER="$HOME/Desktop/Image checker.command"
    printf '#!/bin/sh\nexec "%s/scripts/start.sh"\n' "$DIR" > "$LAUNCHER"
    chmod +x "$LAUNCHER"
    WHERE="double-click \"Image checker\" on your Desktop"
else
    WHERE="run $DIR/scripts/start.sh"
fi

say "Done. To start the app later, $WHERE."
if [ -z "${IMAGECHECK_NO_START:-}" ]; then
    exec scripts/start.sh
fi
