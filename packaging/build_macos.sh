#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
GENERATED_DIR="${PROJECT_DIR}/build/generated"
ICONSET_DIR="${GENERATED_DIR}/Virtus.iconset"
APP_PATH="${PROJECT_DIR}/dist/Virtus AI Chat.app"
APP_EXECUTABLE="${APP_PATH}/Contents/MacOS/Virtus AI Chat"
DMG_ROOT="${PROJECT_DIR}/build/dmg-root"
VERSION="${VIRTUS_BUILD_VERSION:-0.1.0-alpha.1}"
DMG_PATH="${PROJECT_DIR}/dist/Virtus-AI-Chat-${VERSION}-apple-silicon.dmg"
SIGN_IDENTITY="${MACOS_SIGN_IDENTITY:--}"
MINIMUM_MACOS_VERSION="14.0"

mkdir -p "${GENERATED_DIR}"
python "${PROJECT_DIR}/packaging/write_build_metadata.py"

rm -rf "${ICONSET_DIR}"
mkdir -p "${ICONSET_DIR}"
SOURCE_ICON="${PROJECT_DIR}/static/virtus-logo-dark.png"

for size in 16 32 128 256 512; do
  /usr/bin/sips -z "${size}" "${size}" "${SOURCE_ICON}" --out "${ICONSET_DIR}/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  /usr/bin/sips -z "${double}" "${double}" "${SOURCE_ICON}" --out "${ICONSET_DIR}/icon_${size}x${size}@2x.png" >/dev/null
done
/usr/bin/iconutil -c icns "${ICONSET_DIR}" -o "${GENERATED_DIR}/Virtus.icns"

python -m PyInstaller --clean --noconfirm "${PROJECT_DIR}/packaging/virtus_macos.spec"

# PyInstaller's prebuilt bootloader can retain an older LC_BUILD_VERSION even
# when the bundle's LSMinimumSystemVersion is 14. Keep both metadata layers in
# agreement, preserving the SDK version selected by PyInstaller.
SDK_VERSION="$(/usr/bin/xcrun vtool -show-build "${APP_EXECUTABLE}" | /usr/bin/awk '$1 == "sdk" { print $2; exit }')"
if [[ -z "${SDK_VERSION}" ]]; then
  echo "Could not read the frozen executable's macOS SDK version." >&2
  exit 1
fi
PATCHED_EXECUTABLE="${GENERATED_DIR}/Virtus AI Chat.minos"
/usr/bin/xcrun vtool \
  -set-build-version macos "${MINIMUM_MACOS_VERSION}" "${SDK_VERSION}" \
  -replace \
  -output "${PATCHED_EXECUTABLE}" \
  "${APP_EXECUTABLE}"
/bin/chmod 755 "${PATCHED_EXECUTABLE}"
/bin/mv "${PATCHED_EXECUTABLE}" "${APP_EXECUTABLE}"

if [[ "${SIGN_IDENTITY}" == "-" ]]; then
  # Hardened-runtime library validation is incompatible with an ad-hoc team.
  # PyInstaller has already signed nested binaries; seal the bundle without it.
  /usr/bin/codesign --force --deep --sign - "${APP_PATH}"
else
  /usr/bin/codesign --force --deep --options runtime --timestamp --sign "${SIGN_IDENTITY}" "${APP_PATH}"
fi
/usr/bin/codesign --verify --deep --strict "${APP_PATH}"

rm -rf "${DMG_ROOT}"
mkdir -p "${DMG_ROOT}"
/bin/cp -R "${APP_PATH}" "${DMG_ROOT}/Virtus AI Chat.app"
/bin/ln -s /Applications "${DMG_ROOT}/Applications"
rm -f "${DMG_PATH}" "${DMG_PATH}.sha256"
/usr/bin/hdiutil create \
  -volname "Virtus AI Chat" \
  -srcfolder "${DMG_ROOT}" \
  -ov \
  -format UDZO \
  "${DMG_PATH}"
(
  cd "${PROJECT_DIR}/dist"
  /usr/bin/shasum -a 256 "${DMG_PATH:t}" > "${DMG_PATH:t}.sha256"
)

echo "Created ${DMG_PATH}"
