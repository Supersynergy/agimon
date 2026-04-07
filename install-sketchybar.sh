#!/bin/bash
# Install SketchyBar + Claude Monitor plugins
set -e

if ! command -v sketchybar &>/dev/null; then
  echo "Installing SketchyBar..."
  brew tap FelixKratz/formulae
  brew install sketchybar
fi

# Copy config
mkdir -p ~/.config/sketchybar/plugins
cp sketchybar/sketchybarrc ~/.config/sketchybar/sketchybarrc
cp sketchybar/plugins/*.sh ~/.config/sketchybar/plugins/
chmod +x ~/.config/sketchybar/plugins/*.sh

echo "SketchyBar config installed. Start with: brew services start sketchybar"
