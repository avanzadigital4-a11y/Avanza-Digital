"""
jarvis_config.py — Configuración centralizada de JARVIS (modelos + cliente).

POR QUÉ EXISTE:
    Antes, `JARVIS_MODEL` y el cliente `anthropic.Anthropic()` estaban
    duplicados en 14 módulos (jarvis.py, jarvis_whatsapp.py, jarvis_flywheel.py,
    jarvis_mercado.py, jarvis_setter.py, jarvis_leads.py, jarvis_propuestas.py,
    jarvis_proactivo.py, jarvis_memoria.py, jarvis_documentos.py,
    jarvis_reuniones.py, jarvis_dashboard.py, jarvis_emocional.py y
    Jarvis_comunicador.py). Cambiar de modelo obligaba a editar 14 archivos.

    Ahora cada módulo importa desde acá. Cambiar de Sonnet a Opus para una
    función, o actualizar el string del modelo, es tocar UNA línea.

JERARQUÍA DE MODELOS (ver plan de monetización, sección 3):
    liviano → Groq (gratis)   — clasificación, triage, parsing
    medio   → Haiku 4.5        — chat diario, follow-ups, drafts, resúmenes
    pesado  → Sonnet 4.6       — análisis de leads, propuestas, battle cards
    top     → Opus             — solo propuestas críticas (rara vez)

    Regla conservadora: ante la duda, subir de tier. Lo que el aliado le
    muestra al cliente o usa para decidir una venta → tier pesado o top.

NOTA: no se loggea la API key. Si no hay key, get_client() devuelve None y el
llamador usa su fallback heurístico (el producto nunca se cae por la IA).
"""

from __future__ import annotations
import os

# ─── API KEY ──────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

# ─── STRINGS DE MODELO POR TIER (verificados, junio 2026) ─────────────────────
# Groq se resuelve dentro de groq_ai.py; lo dejamos acá como referencia del tier.
MODEL_LIVIANO = "llama-3.3-70b-versatile"      # Groq (gratis) — vía groq_ai.py
MODEL_MEDIO   = "claude-haiku-4-5-20251001"    # Haiku 4.5
MODEL_PESADO  = "claude-sonnet-4-6"            # Sonnet 4.6
MODEL_TOP     = "claude-opus-4-7"              # Opus 4.7 (existe Opus 4.8 si se quiere subir el techo)

# Default histórico del sistema. Antes era "claude-sonnet-4-20250514" (Sonnet 4
# viejo) hardcodeado en 14 lados. Lo dejamos en Sonnet 4.6 (tier pesado) para
# NO degradar calidad: cada módulo puede bajar a MODEL_MEDIO donde convenga.
JARVIS_MODEL  = MODEL_PESADO

JARVIS_TIMEOUT = 15.0

# Mapa tier → string, para `modelo_para("medio")`, etc.
_TIERS = {
    "liviano": MODEL_LIVIANO,
    "medio":   MODEL_MEDIO,
    "pesado":  MODEL_PESADO,
    "top":     MODEL_TOP,
}


def modelo_para(tier: str) -> str:
    """Devuelve el string de modelo para un tier dado. Default: pesado (Sonnet)."""
    return _TIERS.get((tier or "").lower(), MODEL_PESADO)


def is_enabled() -> bool:
    """¿Hay API key de Anthropic configurada?"""
    return bool(ANTHROPIC_API_KEY)


def get_client():
    """
    Devuelve un cliente Anthropic, o None si no hay API key.
    NUNCA lanza: si algo falla, devuelve None y el llamador usa su fallback.
    """
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception:
        return None