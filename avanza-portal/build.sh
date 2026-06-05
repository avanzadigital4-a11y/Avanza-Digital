#!/usr/bin/env bash
# build.sh — Script de build para Render (entorno nativo Python).
set -e

echo ">>> Instalando dependencias Python..."
pip install -r requirements.txt

echo ">>> Build completo."