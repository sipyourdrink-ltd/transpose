#!/bin/bash
# Build Transpose.app into menubar/build/.
#   ./build.sh           build only
#   ./build.sh install   build, copy to ~/Applications and open
set -e
cd "$(dirname "$0")"

APP_NAME="Transpose"
APP_BUNDLE="build/$APP_NAME.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
APP_MACOS="$APP_CONTENTS/MacOS"
RESOURCE_DIR="$APP_CONTENTS/Resources"
INFO_PLIST="$APP_CONTENTS/Info.plist"

rm -rf "$APP_BUNDLE"
mkdir -p "$APP_MACOS" "$RESOURCE_DIR"

swiftc -O Transpose.swift -o "$APP_MACOS/$APP_NAME"

# The app runs the scripts from its own bundle.
cp ../scripts/*.py "$RESOURCE_DIR/"

cat >| "$INFO_PLIST" <<EOL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>ltd.sipyourdrink.transpose</string>
    <key>CFBundleName</key>
    <string>${APP_NAME}</string>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>LSUIElement</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
</dict>
</plist>
EOL

# Ad-hoc signature so the bundle (binary + resources) is sealed as one unit.
codesign --force --sign - "$APP_BUNDLE" >/dev/null 2>&1 || echo "warning: ad-hoc codesign failed; the app is unsigned"

echo "built $APP_BUNDLE"

if [[ "$1" == "install" ]]; then
    echo "Installing to ~/Applications and opening..."
    mkdir -p "$HOME/Applications"
    INSTALL_PATH="$HOME/Applications/$APP_NAME.app"
    rm -rf "$INSTALL_PATH"
    cp -R "$APP_BUNDLE" "$HOME/Applications/"
    open "$INSTALL_PATH"
fi
