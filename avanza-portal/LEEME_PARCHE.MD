# Parche de seguridad — Avanza Partner Portal v1.1

## Qué cambió

### main.py (reemplazar el archivo completo)
- ✅ **API Key middleware**: todos los endpoints de admin requieren `X-API-Key` en el header
- ✅ **POST /admin/setup**: crea el primer admin (solo funciona si no hay ninguno)
- ✅ **POST /aliados/login**: login real con código + contraseña (para el portal del aliado)
- ✅ **GET /aliados/inactivos?dias=30**: aliados sin actividad en N días (para reactivación)
- ✅ **PATCH /aliados/{codigo}/nivel**: cambio manual de nivel (faltaba, admin.html lo usaba)
- ✅ **GET /dashboard** ahora incluye `leaderboard` (top 10 por ventas en 6 meses)
- 🐛 **Fix crítico**: `/aliados/suspendidos` e `/aliados/inactivos` ahora van ANTES de
  `/aliados/{codigo}` — antes FastAPI los trataba como un código de aliado

### admin.html (parcheado por apply_patch.py)
- ✅ Campo "API Key de admin" en el formulario de login
- ✅ Todas las llamadas `fetch()` → `apiFetch()` que inyecta `X-API-Key` automáticamente

### portal.html (parcheado por apply_patch.py)
- ✅ Login usa `POST /aliados/login` con contraseña real — antes solo verificaba que el código existiera

---

## Instrucciones de deploy (paso a paso)

### Paso 1 — Configurar la API Key en Railway
```
Railway → tu proyecto → Variables → New Variable
Nombre:  ADMIN_API_KEY
Valor:   [tu clave secreta, ej: avanza-admin-2026-xK9mP#8]
```

### Paso 2 — Aplicar el parche a los HTML
```bash
cd avanza-portal
python3 apply_patch.py
```
Esto genera `admin.html.bak` y `portal.html.bak` como respaldo.

### Paso 3 — Hacer push a Railway
```bash
git add main.py admin.html portal.html apply_patch.py .env.example
git commit -m "fix: seguridad API key + login real + endpoint inactivos + fix rutas"
git push
```
Railway hace el redeploy automáticamente.

### Paso 4 — Crear el primer admin
Una vez deployado, llamar a:
```
POST https://avanza-digital-production.up.railway.app/admin/setup?username=ivan&password=TU_PASS_ADMIN
Header: X-API-Key: tu-clave-secreta
```
Desde curl:
```bash
curl -X POST "https://avanza-digital-production.up.railway.app/admin/setup?username=ivan&password=TU_PASS_ADMIN" \
     -H "X-API-Key: tu-clave-secreta"
```
Este endpoint queda bloqueado automáticamente después de la primera ejecución.

### Paso 5 — Verificar
1. Abrir admin.html en el browser
2. Login: usuario `ivan`, contraseña `TU_PASS_ADMIN`, API key `tu-clave-secreta`
3. Verificar que carga el dashboard
4. Abrir portal.html → Login con código `AL-001` y contraseña del aliado

---

## Nuevos endpoints

### GET /aliados/inactivos?dias=30
Devuelve aliados sin referidos ni ventas en los últimos N días.
Respuesta incluye `dias_inactivo` para el sistema de reactivación.

```json
{
  "filtro_dias": 30,
  "total": 8,
  "aliados": [
    {
      "codigo": "AL-003",
      "nombre": "Carlos Martínez",
      "whatsapp": "3424000000",
      "dias_inactivo": 45,
      "nivel": "BASIC",
      "ventas_totales": 0
    }
  ]
}
```

### GET /dashboard (nuevo campo)
Ahora incluye `leaderboard`: top 10 aliados por ventas en los últimos 6 meses.

### POST /aliados/login
Login del aliado con verificación real de contraseña.
```
POST /aliados/login?codigo=AL-001&password=avanza2026
```

### PATCH /aliados/{codigo}/nivel
Cambio manual de nivel por el admin.
```
PATCH /aliados/AL-001/nivel?nivel=SILVER
Header: X-API-Key: tu-clave
```

---

## Contraseñas de aliados existentes
Los 16 aliados existentes tienen `password_hash` en la DB (generado al importar).
La contraseña por defecto es `avanza2026`.

Para cambiar la contraseña de un aliado, usar directamente la DB de Railway:
```sql
UPDATE aliados
SET password_hash = '<nuevo hash bcrypt>'
WHERE codigo = 'AL-001';
```
O agregar un endpoint `POST /aliados/{codigo}/cambiar-password` (no incluido en este parche).