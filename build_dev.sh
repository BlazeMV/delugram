#!/bin/bash

# Function to print usage
print_usage() {
    echo "Usage: $0 [-v] [config_dir]"
    echo "  -v: Rebuild vendor directory"
    echo "  config_dir: Optional deluge config directory (defaults to ~/.config/deluge)"
}

# Parse options
REBUILD_VENDOR=false
while getopts "v" opt; do
    case $opt in
        v)
            REBUILD_VENDOR=true
            ;;
        \?)
            print_usage
            exit 1
            ;;
    esac
done

# Shift past the options to get the remaining arguments
shift $((OPTIND-1))

# Get base directory
BASEDIR=$(cd "$(dirname "$0")" && pwd)

# Set config directory from argument or default
CONFIG_DIR=$(test -z "$1" && echo "$HOME/.config/deluge" || echo "$1")

# Check if config directory exists and is valid
[ -d "$CONFIG_DIR/plugins" ] || echo "Config dir $CONFIG_DIR is either not a directory or is not a proper deluge config directory. Exiting"
[ -d "$CONFIG_DIR/plugins" ] || exit 1

cd "$BASEDIR"

# Handle vendor directory rebuild if -v option is passed
if [ "$REBUILD_VENDOR" = true ]; then
    echo "Rebuilding vendor directory..."
    rm -rf "$BASEDIR/delugram/vendor"
    mkdir -p "$BASEDIR/delugram/vendor"
    touch "$BASEDIR/delugram/vendor/__init__.py"
    cp -r "$BASEDIR/.venv/lib/python3.12/site-packages/"* "$BASEDIR/delugram/vendor/"
fi

# Remove any existing links
rm -rf "$CONFIG_DIR/plugins"/*.egg-link
rm -rf "$BASEDIR/build"
rm -rf "$BASEDIR/dist"
rm -rf "$BASEDIR"/*.egg-info

# Create temp directory if it doesn't exist
test -d "$BASEDIR/temp" || mkdir "$BASEDIR/temp"

# Set Python path
export PYTHONPATH="$BASEDIR/temp:$PYTHONPATH"

# Build and develop
"$BASEDIR/.venv/bin/python" setup.py build develop --install-dir "$BASEDIR/temp"

# Copy egg links and clean up
cp "$BASEDIR/temp/"*.egg-link "$CONFIG_DIR/plugins"
rm -fr "$BASEDIR/temp"