#!/bin/bash
BASEDIR=$(cd "$(dirname "$0")" && pwd)
CONFIG_DIR=$( test -z "$1" && echo "$HOME/.config/deluge" || echo "$1")
[ -d "$CONFIG_DIR/plugins" ] || echo "Config dir $CONFIG_DIR is either not a directory or is not a proper deluge config directory. Exiting"
[ -d "$CONFIG_DIR/plugins" ] || exit 1
cd "$BASEDIR"
# Remove any existing links
rm -rf "$CONFIG_DIR/plugins"/*.egg-link
rm -rf "$BASEDIR/build"
rm -rf "$BASEDIR/dist"
rm -rf "$BASEDIR"/*.egg-info
test -d "$BASEDIR/temp" || mkdir "$BASEDIR/temp"
export PYTHONPATH="$BASEDIR/temp:$PYTHONPATH"
"$BASEDIR/.venv/bin/python" setup.py build develop --install-dir "$BASEDIR/temp"
cp "$BASEDIR/temp/"*.egg-link "$CONFIG_DIR/plugins"
rm -fr "$BASEDIR/temp"