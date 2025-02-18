#!/bin/bash

# Function to print usage
print_usage() {
    echo "Usage: $0 <dev|prod> [-v] [config_dir]"
    echo "  dev: Build in development mode (.egg-link)"
    echo "  prod: Build in production mode (.egg)"
    echo "  -v: Rebuild vendor directory (only allowed in dev mode)"
    echo "  config_dir: Optional deluge config directory (defaults to ~/.config/deluge)"
}

# Check if at least one argument is provided
if [ $# -lt 1 ]; then
    print_usage
    exit 1
fi

# Extract the first argument as the build mode
BUILD_MODE=$1
shift

# Validate build mode
if [[ "$BUILD_MODE" != "dev" && "$BUILD_MODE" != "prod" ]]; then
    echo "Error: Invalid build mode. Use 'dev' or 'prod'."
    print_usage
    exit 1
fi

# Parse options
REBUILD_VENDOR=false
while getopts "v" opt; do
    case $opt in
        v)
            if [ "$BUILD_MODE" != "dev" ]; then
                echo "Error: -v option is only allowed in dev mode."
                exit 1
            fi
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
CONFIG_DIR=${1:-"$HOME/.config/deluge"}

# Check if config directory exists and is valid
if [ ! -d "$CONFIG_DIR/plugins" ]; then
    echo "Config dir $CONFIG_DIR is either not a directory or is not a proper deluge config directory. Exiting"
    exit 1
fi

cd "$BASEDIR"

# Handle vendor directory rebuild if -v option is passed
if [ "$REBUILD_VENDOR" = true ]; then
    echo "Rebuilding vendor directory..."
    rm -rf "$BASEDIR/delugram/vendor"
    mkdir -p "$BASEDIR/delugram/vendor"
    touch "$BASEDIR/delugram/vendor/__init__.py"
    cp -r "$BASEDIR/.venv/lib/python3.12/site-packages/"* "$BASEDIR/delugram/vendor/"
fi

# Remove any existing links and build artifacts
rm -rf "$CONFIG_DIR/plugins"/*.egg-link
rm -rf "$BASEDIR/build"
rm -rf "$BASEDIR/dist"
rm -rf "$BASEDIR"/*.egg-info

if [ "$BUILD_MODE" = "dev" ]; then
    echo "Building in development mode..."

    # Create temp directory for develop mode
    mkdir -p "$BASEDIR/temp"

    # Set Python path for develop mode
    export PYTHONPATH="$BASEDIR/temp:$PYTHONPATH"

    # Build in development mode
    "$BASEDIR/.venv/bin/python" setup.py build develop --install-dir "$BASEDIR/temp"

    # Copy egg links to Deluge plugins directory
    cp "$BASEDIR/temp/"*.egg-link "$CONFIG_DIR/plugins"

    # Cleanup temp directory
    rm -fr "$BASEDIR/temp"

else
    echo "Building in production mode..."

    # Build in production mode (generate .egg)
    "$BASEDIR/.venv/bin/python" setup.py bdist_egg

    echo "Production build complete. Egg package can be found in the dist/ directory."
fi
