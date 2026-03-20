"""
importar_aliados.py
Ejecutar desde la carpeta avanza-portal:
    python importar_aliados.py
"""

import requests
import time

API = "http://127.0.0.1:8000"

# Los 15 aliados del Excel
ALIADOS = [
    {
        "nombre":     "Morena Alejandra Altamiranda Ganini",
        "dni":        "46.128.865",
        "email":      "morealtamiranda24@gmail.com",
        "whatsapp":   "+54 9 3548 414273",
        "ciudad":     "La Falda, Córdoba",
        "perfil":     "Closer de ventas B2B",
        "fecha_firma":"12/03/2026",
    },
    {
        "nombre":     "Maximiliano Lionel Villafañe",
        "dni":        "48.121.122",
        "email":      "maximovillafane@gmail.com",
        "whatsapp":   "+54 9 3813 658051",
        "ciudad":     "San Miguel de Tucumán",
        "perfil":     "Generación de clientes",
        "fecha_firma":"12/03/2026",
    },
    {
        "nombre":     "Alejandro Ezequiel Vyhñak",
        "dni":        "48576415",
        "email":      "avyhnak@gmail.com",
        "whatsapp":   "+54 911 32374824",
        "ciudad":     "Ramos Mejía, Buenos Aires",
        "perfil":     "Estudiante universitario",
        "fecha_firma":"16/03/2026",
    },
    {
        "nombre":     "Suarez Alejandro M",
        "dni":        "34092505",
        "email":      "suarezalejandro@avanza.ref",  # sin email en contrato
        "whatsapp":   "",
        "ciudad":     "",
        "perfil":     "",
        "fecha_firma":"16/03/2026",
    },
    {
        "nombre":     "Vera Diego Ezequiel",
        "dni":        "43747608",
        "email":      "veraaeze@gmail.com",
        "whatsapp":   "+54 9 3795 024193",
        "ciudad":     "Corrientes Capital",
        "perfil":     "",
        "fecha_firma":"17/03/2026",
    },
    {
        "nombre":     "Margheritta Ramiro",
        "dni":        "50-075-348",
        "email":      "margherittaramiro@gmail.com",
        "whatsapp":   "+54 221 495-0961",
        "ciudad":     "Buenos Aires, La Plata",
        "perfil":     "Asistente de ventas",
        "fecha_firma":"17/03/2026",
    },
    {
        "nombre":     "Kevin David Celiz",
        "dni":        "41564930",
        "email":      "celizdavid86@gmail.com",
        "whatsapp":   "",
        "ciudad":     "Buenos Aires",
        "perfil":     "Generación de leads y ventas digitales",
        "fecha_firma":"17/03/2026",
    },
    {
        "nombre":     "Axel Amieva",
        "dni":        "39393769",
        "email":      "axelamieva@avanza.ref",  # sin email en contrato
        "whatsapp":   "",
        "ciudad":     "",
        "perfil":     "",
        "fecha_firma":"17/03/2026",
    },
    {
        "nombre":     "Marco Alexander Cáceres García",
        "dni":        "20-95758318-1",
        "email":      "marcoalex270@gmail.com",
        "whatsapp":   "+34614587345",
        "ciudad":     "Buenos Aires",
        "perfil":     "Técnico en administración",
        "fecha_firma":"17/03/2026",
    },
    {
        "nombre":     "Lucas Lopez",
        "dni":        "50824332",
        "email":      "lucaslopez20117@gmail.com",
        "whatsapp":   "",
        "ciudad":     "Buenos Aires",
        "perfil":     "",
        "fecha_firma":"19/03/2026",
    },
    {
        "nombre":     "Leonel Martinez Mazzoconi",
        "dni":        "42454483",
        "email":      "leonelmartinez@avanza.ref",  # sin email en contrato
        "whatsapp":   "",
        "ciudad":     "",
        "perfil":     "",
        "fecha_firma":"",
    },
    {
        "nombre":     "Jose Angel Zambrano",
        "dni":        "27242828",
        "email":      "jazsrm7@gmail.com",
        "whatsapp":   "+584124555382",
        "ciudad":     "Venezuela, Estado Miranda, Cua",
        "perfil":     "Socio estrategico",
        "fecha_firma":"19/03/2026",
    },
    {
        "nombre":     "Guillermo Santellan",
        "dni":        "20240319528",
        "email":      "guillermosantellan3@gmail.com",
        "whatsapp":   "3855101222",
        "ciudad":     "Santiago del Estero",
        "perfil":     "Closer de elite",
        "fecha_firma":"19/03/2026",
    },
    {
        "nombre":     "Santiago Escudero Nicolas",
        "dni":        "47.267.862 / 20-47267862-1",
        "email":      "santiagoescudero257@gmail.com",
        "whatsapp":   "+54 266 512 6537",
        "ciudad":     "San Luis",
        "perfil":     "Closer de ventas",
        "fecha_firma":"18/03/2026",
    },
    {
        "nombre":     "Maximiliano Ezequiel Torrez",
        "dni":        "20-39941229-4",
        "email":      "ezequiel.closer.ventas@gmail.com",
        "whatsapp":   "2302694127",
        "ciudad":     "La Pampa",
        "perfil":     "",
        "fecha_firma":"19/03/2026",
    },
]

def importar():
    print("\n🚀 Iniciando importación de aliados a Avanza Partner System\n")
    print("─" * 60)

    exitosos = 0
    fallidos  = 0
    saltados  = 0

    for a in ALIADOS:
        params = {
            "nombre":      a["nombre"],
            "email":       a["email"],
            "whatsapp":    a.get("whatsapp", ""),
            "ciudad":      a.get("ciudad", ""),
            "dni":         a.get("dni", ""),
            "perfil":      a.get("perfil", ""),
            "fecha_firma": a.get("fecha_firma", ""),
            "password":    "avanza2026",
        }

        try:
            res = requests.post(f"{API}/aliados/crear", params=params, timeout=10)
            data = res.json()

            if res.status_code == 200:
                print(f"  ✅ {data['codigo']} — {a['nombre']}")
                print(f"     Link: {data['link_ref']}")
                exitosos += 1
            elif "Ya existe" in str(data.get("detail", "")):
                print(f"  ⚠️  SALTADO — {a['nombre']} (ya existe en el sistema)")
                saltados += 1
            else:
                print(f"  ❌ ERROR — {a['nombre']}: {data.get('detail','Error desconocido')}")
                fallidos += 1

        except requests.exceptions.ConnectionError:
            print(f"\n❌ No se puede conectar con el servidor.")
            print("   Asegurate de que esté corriendo: uvicorn main:app --reload")
            return
        except Exception as e:
            print(f"  ❌ ERROR — {a['nombre']}: {str(e)}")
            fallidos += 1

        time.sleep(0.1)  # pequeña pausa entre requests

    print("\n" + "─" * 60)
    print(f"  ✅ Importados exitosamente: {exitosos}")
    print(f"  ⚠️  Saltados (ya existían):  {saltados}")
    print(f"  ❌ Fallidos:                {fallidos}")
    print(f"  📊 Total procesados:        {exitosos + saltados + fallidos}")
    print("─" * 60)

    if exitosos > 0:
        print(f"\n🎉 ¡Listo! Los aliados ya están en el sistema.")
        print(f"   Abrí admin.html y verificalos en la tab Aliados.\n")


if __name__ == "__main__":
    importar()