import os
from bs4 import BeautifulSoup

# Configuración: Archivos a auditar
archivos = ['index.html', 'recursos.html']

def auditar_seo(archivo):
    if not os.path.exists(archivo):
        print(f"❌ Archivo no encontrado: {archivo}")
        return

    with open(archivo, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f, 'html.parser')
        
    print(f"\n🔎 AUDITORÍA DE: {archivo.upper()}")
    print("="*40)

    # 1. Verificar Title (Longitud óptima 50-60 caracteres)
    title = soup.title.string if soup.title else None
    if title:
        largo = len(title)
        estado = "✅" if 50 <= largo <= 65 else "⚠️ (Muy corto/largo)"
        print(f"{estado} Title ({largo} chars): {title}")
    else:
        print("❌ FALTA TITLE TAG")

    # 2. Verificar Meta Description
    desc = soup.find('meta', attrs={'name': 'description'})
    if desc:
        content = desc.get('content', '')
        largo = len(content)
        estado = "✅" if 120 <= largo <= 160 else "⚠️ (Ajustar a 120-160)"
        print(f"{estado} Descrip ({largo} chars): {content[:50]}...")
    else:
        print("❌ FALTA META DESCRIPTION")

    # 3. Verificar Jerarquía H1 (Solo debe haber uno)
    h1s = soup.find_all('h1')
    if len(h1s) == 1:
        print(f"✅ H1 Único: {h1s[0].get_text(strip=True)}")
    elif len(h1s) == 0:
        print("❌ NO HAY H1 (Grave)")
    else:
        print(f"⚠️ MÚLTIPLES H1 ({len(h1s)} encontrados). Deja solo uno.")

    # 4. Verificar Imágenes sin ALT
    imgs = soup.find_all('img')
    imgs_sin_alt = [img for img in imgs if not img.get('alt')]
    if not imgs_sin_alt:
        print(f"✅ Todas las imágenes ({len(imgs)}) tienen texto ALT.")
    else:
        print(f"⚠️ {len(imgs_sin_alt)} imágenes sin ALT (Accesibilidad/SEO).")

    # 5. Detección de Schema.org
    schemas = soup.find_all('script', attrs={'type': 'application/ld+json'})
    if schemas:
        print(f"✅ Schema Markup detectado ({len(schemas)} bloques).")
    else:
        print("⚠️ No se detectó Schema JSON-LD.")

if __name__ == "__main__":
    print("🚀 INICIANDO AUDITORÍA SEO AUTOMATIZADA - AVANZA DIGITAL")
    for archivo in archivos:
        auditar_seo(archivo)
    print("\n✅ Auditoría finalizada.")