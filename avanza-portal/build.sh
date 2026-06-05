#!/usr/bin/env bash
# build.sh — Script de build para Render (corre como root, sin sudo).
set -e

echo ">>> Instalando dependencias de sistema para WeasyPrint..."
apt-get update -qq
apt-get install -y --no-install-recommends \
  libpango-1.0-0 \
  libpangocairo-1.0-0 \
  libpangoft2-1.0-0 \
  libgdk-pixbuf-2.0-0 \
  libcairo2 \
  libffi8 \
  libharfbuzz0b \
  libjpeg62-turbo \
  libopenjp2-7 \
  shared-mime-info \
  fonts-dejavu-core

echo ">>> Instalando dependencias Python..."
pip install -r requirements.txt

echo ">>> Build completo."