#!/bin/sh
# Installe fitsview pour l'utilisateur courant (sans droits administrateur).
#   sh installer.sh            installer ou mettre à jour
#   sh installer.sh --retirer  désinstaller
set -e

DEST="$HOME/.local/share/fitsview"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor"
HERE=$(cd "$(dirname "$0")" && pwd)

maj_caches() {
    update-desktop-database "$APPS" 2>/dev/null || true
    gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null || true
}

if [ "$1" = "--retirer" ]; then
    rm -rf "$DEST"
    rm -f "$APPS/fitsview.desktop" "$ICONS/scalable/apps/fitsview.svg" \
          "$ICONS/256x256/apps/fitsview.png"
    maj_caches
    echo "fitsview a été désinstallé."
    exit 0
fi

for f in fitsview.py fitsview.svg; do
    if [ ! -f "$HERE/$f" ]; then
        echo "Fichier manquant : $f (placez-le à côté de installer.sh)" >&2
        exit 1
    fi
done

# retire une éventuelle installation sous l'ancien nom « Tamis »
rm -rf "$HOME/.local/share/tamis"
rm -f "$APPS/tamis.desktop" "$ICONS/scalable/apps/tamis.svg" \
      "$ICONS/256x256/apps/tamis.png"

mkdir -p "$DEST" "$APPS" "$ICONS/scalable/apps" "$ICONS/256x256/apps"
cp "$HERE/fitsview.py" "$HERE/fitsview.svg" "$DEST/"
chmod +x "$DEST/fitsview.py"
cp "$HERE/fitsview.svg" "$ICONS/scalable/apps/fitsview.svg"
if [ -f "$HERE/fitsview.png" ]; then
    cp "$HERE/fitsview.png" "$DEST/"
    cp "$HERE/fitsview.png" "$ICONS/256x256/apps/fitsview.png"
fi

cat > "$APPS/fitsview.desktop" <<FIN
[Desktop Entry]
Type=Application
Name=fitsview
GenericName=Visionneuse FITS
Comment=Trier ses brutes FITS d'astrophotographie
Exec=python3 "$DEST/fitsview.py" %F
Icon=fitsview
Terminal=false
Categories=Graphics;Science;Astronomy;Viewer;
MimeType=image/fits;application/fits;
Keywords=FITS;astro;astrophotographie;SER;tri;
StartupWMClass=fitsview
FIN

maj_caches
echo "fitsview est installé : cherchez « fitsview » dans le menu des applications."
