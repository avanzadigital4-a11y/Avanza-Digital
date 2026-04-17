# Avanza Partner Portal — v1.3

## Qué trae esta versión

Esta actualización agrega todo lo que faltaba para convertir el portal en
**infraestructura de ventas B2B distribuida**. El backend quedó en 72 rutas
(antes 48), el portal en 10 tabs (antes 9) y se agregaron 4 tablas nuevas.

### A — Perfilado IA de leads
- Endpoint: `POST /prospectos/{id}/perfilar?rubro=X&tamano=Y&urgencia=Z`
- Devuelve `score` (0-100), `plan_recomendado`, `pitch_sugerido`, `ticket_esperado`.
- Heurística local, determinística, sin llamadas a LLM externo (cero costo
  operativo, cero latencia, sin riesgo de prompt injection).
- En el portal: botón 🧠 **Perfilar IA** en cada card de prospecto → modal con
  3 selects y resultado con copiar pitch al portapapeles.

### B — Piloto automático real
- Scheduler corre cada hora (`job_piloto_automatico`).
- Si un prospecto tiene `piloto_automatico = True`, envía hasta 3 emails
  espaciados 3 días (`PILOTO_INTERVALO_DIAS`). Se apaga solo si el lead
  responde o si se supera `PILOTO_MAX_PASOS`.
- Tabla nueva `automation_log` guarda cada envío.
- Endpoint: `GET /aliados/{codigo}/automation-log`
- El botón 🤖 en cada prospecto del portal activa/desactiva el piloto.

### C — Sistema de reputación
- Endpoints: `GET /aliados/{codigo}/reputacion`, `GET /admin/reputacion/ranking`
- Score 0-100 calculado con 5 factores ponderados: tasa de cierre (40%),
  éxito en bolsa (20%), velocidad (20%), actividad reciente (10%), red activa
  (10%).
- 6 badges: CLOSER 🎯, RAPIDO ⚡, FIEL 🔥, TOP_TICKET 💎, EMBAJADOR 👑,
  BOLSA_MASTER 🏆
- Se muestran en el dashboard del portal en una card dedicada.

### D — Marketplace de leads pago
- Tres tiers en la bolsa: `basico` (gratis), `calificado` ⭐, `premium` 💎.
- Los calificados/premium se pagan con **créditos**.
- Endpoints:
  - `GET /bolsa/marketplace?codigo_aliado=XXX`
  - `POST /bolsa/{id}/comprar?codigo_aliado=XXX`
  - `GET /aliados/{codigo}/creditos`
  - `POST /admin/aliados/{codigo}/creditos?delta=50&motivo=recarga_admin`
  - `POST /admin/bolsa-v2` (con tier, costo_creditos, score_calidad, notas)
- Tabla `transacciones_credito` auditada por aliado.
- UI: sección "Leads Premium" dentro del tab Bolsa, con saldo visible y botón
  "Comprar".
- Admin: botón 💰 en cada fila de aliado para asignar/quitar créditos.

### E — Financiación / cuotas
- Endpoint: `GET /cotizador/cuotas?plan=Plan+Pro&cuotas=6`
- 4 opciones: 1 (sin recargo), 3 (+8%), 6 (+15%), 12 (+28%).
- Selector agregado en el cotizador con cálculo en vivo.

### F — Comunidad interna
- Feed público con 3 tipos de posts: Tip 💡, Win 🎉, Pregunta ❓
- Likes y comentarios.
- Moderación admin: `POST /admin/comunidad/{id}/fijar` y `/ocultar`.
- Tab nuevo "Comunidad" en el portal.

### G — Portal público por aliado (marca blanca lite)
- URL pública: `GET /p/{ref_code}`
- Landing HTML con nombre/bio del aliado + CTA de pago con atribución.
- Configurable: `PATCH /aliados/{codigo}/portal-publico?activo=true&titular=X&bio=Y`

---

## Deploy

El código ya es idempotente — las migraciones corren solas al iniciar:

```bash
git add main.py models.py portal.html admin.html
git commit -m "v1.3: Perfilado IA + Reputación + Marketplace + Comunidad"
git push   # Railway auto-redeploya
```

No hay breaking changes:
- Todos los endpoints viejos siguen funcionando.
- Las nuevas columnas tienen defaults razonables.
- El scheduler agrega el job de piloto sin tocar el de notif 24h.

### Variables de entorno necesarias

Mismas que antes:
- `DATABASE_URL` (Railway lo inyecta solo con plugin Postgres)
- `ADMIN_API_KEY`
- `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `EMAIL_FROM` (para piloto automático)
- `MP_ACCESS_TOKEN` (checkout)
- `PORTAL_URL`

---

## Qué quedó fuera (intencionalmente)

| # | Feature | Por qué no |
|---|---|---|
| - | **LLM real para perfilado** | Agrega costo por request + latencia + riesgo. La heurística actual es explicable y gratis. Si querés activarlo después, el punto de extensión es `_perfilar_prospecto()`. |
| - | **WhatsApp en piloto automático** | Requiere integrar WhatsApp Business API o Twilio (~50 USD/mes mínimo). El piloto por email ya cubre el 80%. |
| - | **Marca blanca multi-tenant completa** | Reescribe el auth y el modelo. El `/p/{ref_code}` cubre el 90% del caso de uso (aliados con su propia landing). |
| - | **Moderación UI completa en admin** | Los endpoints existen (`/admin/comunidad/*`). Se puede usar curl o agregar tab admin después — es HTML puro. |

---

## Árbol de archivos

```
avanza-portal/
├─ main.py           # Backend FastAPI — 2.217 líneas, 72 rutas
├─ models.py         # SQLAlchemy — 12 tablas (4 nuevas)
├─ database.py       # Engine config (sin cambios)
├─ portal.html       # Portal del aliado — 10 tabs
├─ admin.html        # Panel admin
├─ requirements.txt
├─ Procfile
├─ runtime.txt
├─ importar_aliados.py  # Script de importación inicial (sin cambios)
└─ LEEME_v13.md      # Este archivo
```

## Sobre el JS del portal

Todas las funciones nuevas están al final del `<script>` (buscar comentario
`v1.3 — REPUTACIÓN, CRÉDITOS...`). Las funciones son:

- `cargarReputacion()` — llena la card del dashboard
- `cargarCreditos()` — llena el saldo
- `actualizarCuotas()` — simulador de cuotas en el cotizador
- `abrirPerfilado(id, nombre)` / `ejecutarPerfilado()` / `copiarPitch()` — modal de IA
- `cargarMarketplace()` / `comprarLead(id, costo)` — leads pagos
- `cargarComunidad()` / `publicarPost()` / `darLike(id)` / `comentarPost(id)` — feed
