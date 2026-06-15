#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# rehash.sh — Actualiza los hashes de los módulos JS tras editar su contenido.
#
# USO:
#   bash rehash.sh            (desde la carpeta avanza-portal/)
#
# QUÉ HACE:
#   Para cada archivo  assets/js/portal.<módulo>.<hash>.js :
#     1. Calcula el SHA-256 real del contenido del archivo (primeros 10 hex).
#     2. Si el hash en el nombre de archivo YA coincide → no toca nada.
#     3. Si el contenido cambió → renombra el archivo con el hash correcto
#        y actualiza TODAS las referencias en portal.html automáticamente.
#
# POR QUÉ IMPORTA:
#   Netlify sirve los archivos de assets/js/ con Cache-Control: immutable (1 año).
#   Si el nombre no cambia, el navegador usa la copia vieja del disco sin preguntar.
#   Al cambiar el hash en el nombre, la URL es nueva → el browser la descarga sí o sí.
#   Resultado: todos los usuarios ven el cambio al hacer F5, sin necesidad de Ctrl+Shift+R.
#
# ARCHIVOS QUE TOCA:
#   • assets/js/portal.*.*.js   (renombra los que cambiaron)
#   • portal.html               (actualiza src=, file:, y comentarios)
#
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuración ─────────────────────────────────────────────────────────────
JS_DIR="assets/js"
HTML_FILE="portal.html"

# ── Validaciones previas ──────────────────────────────────────────────────────
if [ ! -d "$JS_DIR" ]; then
  echo "❌  No se encontró la carpeta $JS_DIR"
  echo "    Ejecutá el script desde dentro de la carpeta avanza-portal/"
  exit 1
fi

if [ ! -f "$HTML_FILE" ]; then
  echo "❌  No se encontró $HTML_FILE"
  echo "    Ejecutá el script desde dentro de la carpeta avanza-portal/"
  exit 1
fi

# ── Procesamiento ─────────────────────────────────────────────────────────────
CHANGED=0
SKIPPED=0

echo ""
echo "╔══════════════════════════════════════════════════╗"
echo "║          rehash.sh — Avanza Digital              ║"
echo "╚══════════════════════════════════════════════════╝"
echo ""
echo "📂  Revisando archivos en $JS_DIR ..."
echo ""

for filepath in "$JS_DIR"/portal.*.*.js; do
  # Saltar si el glob no matcheó ningún archivo
  [ -f "$filepath" ] || continue

  filename=$(basename "$filepath")

  # Validar que el nombre sigue el patrón portal.<módulo>.<10hex>.js
  # Ejemplos: portal.core.c03b8c9779.js  /  portal.jarvis.7fce656e4b.js
  if [[ "$filename" =~ ^(portal\.[a-z]+)\.([0-9a-f]{10})\.js$ ]]; then
    base="${BASH_REMATCH[1]}"       # → portal.core
    old_hash="${BASH_REMATCH[2]}"   # → c03b8c9779
  else
    echo "  ⚠️  $filename  →  nombre fuera del patrón, se omite"
    continue
  fi

  # Calcular hash real del contenido actual del archivo
  new_hash=$(sha256sum "$filepath" | cut -c1-10)

  if [ "$old_hash" = "$new_hash" ]; then
    echo "  ✅  $filename  →  sin cambios"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # El contenido cambió: renombrar y actualizar HTML
  new_filename="${base}.${new_hash}.js"
  new_filepath="$JS_DIR/$new_filename"

  echo "  🔄  $filename"
  echo "      └─▶  $new_filename"

  mv "$filepath" "$new_filepath"

  # Reemplaza TODAS las apariciones del nombre viejo en portal.html:
  #   • <script src="assets/js/portal.core.HASH.js">
  #   • file: 'portal.jarvis.HASH.js'   (lazy-loading de Jarvis)
  #   • comentarios <!-- ... portal.jarvis.HASH.js ... -->
  sed -i "s|${filename}|${new_filename}|g" "$HTML_FILE"

  echo "      ✏️   portal.html actualizado"
  CHANGED=$((CHANGED + 1))
done

# ── Resumen ───────────────────────────────────────────────────────────────────
echo ""
echo "─────────────────────────────────────────────────────"
if [ "$CHANGED" -eq 0 ]; then
  echo "✨  Todo al día. Los $SKIPPED módulo(s) no tuvieron cambios."
  echo "    No se modificó nada."
else
  echo "✅  $CHANGED módulo(s) actualizado(s) con hash nuevo."
  [ "$SKIPPED" -gt 0 ] && echo "    $SKIPPED módulo(s) sin cambios (se dejaron igual)."
  echo ""
  echo "    Próximos pasos:"
  echo "      git add assets/js/ portal.html"
  echo "      git commit -m 'build: rehash módulos JS'"
  echo "      git push"
  echo ""
  echo "    Todos los usuarios verán el cambio con un F5 normal. ✔"
fi
echo ""