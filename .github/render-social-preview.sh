#!/bin/sh
# Regenerate .github/social-preview.png from the SVG source (needs rsvg-convert).
cd "$(dirname "$0")" && rsvg-convert -w 1280 -h 640 social-preview.svg -o social-preview.png
