"""
jarvis_llm.py — Capa única de IA multi-proveedor para JARVIS.

QUÉ HACE
    Reemplaza a Claude (Anthropic) por proveedores con free tier, manteniendo
    EXACTAMENTE la misma interfaz que ya usaban los 14 módulos jarvis_*:

        client = get_client()                  # o anthropic.Anthropic(...)
        msg = client.messages.create(
            model=..., max_tokens=..., system=...,
            messages=[{"role": "user", "content": "..."}],
        )
        texto = msg.content[0].text

    Internamente rutea a Gemini / Groq / OpenRouter (todos hablan formato
    OpenAI-compatible) con CADENA DE FALLBACK: si un proveedor falla o agota su
    cuota gratis, prueba el siguiente. Si todos fallan, levanta excepción y el
    módulo llamador cae a su fallback heurístico (el producto nunca se cae por IA).

POR QUÉ UN SHIM Y NO EDITAR 14 ARCHIVOS
    Todos los módulos hacen `.messages.create(...)` → `.content[0].text`.
    Imitamos esa forma exacta y, con install(), parcheamos anthropic.Anthropic
    para que los módulos que instancian el cliente directo también ruteen acá.
    Cambio mínimo, reversible: sin keys gratis configuradas, no toca nada.

PROVEEDORES (orden configurable por env JARVIS_LLM_ORDER, default abajo)
    gemini      → GEMINI_API_KEY (o GOOGLE_API_KEY)  modelo: gemini-2.5-flash
    groq        → GROQ_API_KEY                        modelo: llama-3.3-70b-versatile
    openrouter  → OPENROUTER_API_KEY                  modelo: meta-llama/llama-4-scout:free

    Cada modelo se puede override por env sin tocar código:
        GEMINI_MODEL, GROQ_MODEL, OPENROUTER_MODEL

NOTA: nunca se loggean las API keys.
"""

from __future__ import annotations
import os
import sys
from typing import Optional, List, Dict, Any

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # el shim levantará excepción y el módulo usará su fallback

# Sentinel para que los guards `if not ANTHROPIC_API_KEY` de los módulos pasen
# sin necesitar una key real de Anthropic. El cliente parcheado IGNORA su valor.
_SENTINEL = "FREE-LLM-JARVIS"

# Orden por defecto de la cadena de fallback.
DEFAULT_ORDER = ["gemini", "groq", "openrouter"]

# Definición base de cada proveedor. base_url SIN "/chat/completions" (se agrega).
_PROVIDER_DEFS: Dict[str, Dict[str, Any]] = {
    "gemini": {
        "env_keys": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
        "model_env": "GEMINI_MODEL",
    },
    "groq": {
        "env_keys": ["GROQ_API_KEY"],
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "model_env": "GROQ_MODEL",
    },
    "openrouter": {
        "env_keys": ["OPENROUTER_API_KEY"],
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "meta-llama/llama-4-scout:free",
        "model_env": "OPENROUTER_MODEL",
    },
}

DEFAULT_TIMEOUT = float(os.environ.get("JARVIS_TIMEOUT", "20") or 20)


# ─── Detección de proveedores activos ────────────────────────────────────────
def _key_for(pdef: Dict[str, Any]) -> str:
    for env in pdef["env_keys"]:
        v = os.environ.get(env, "").strip()
        if v:
            return v
    return ""


def _active_providers() -> List[Dict[str, Any]]:
    """Lista ordenada de proveedores que tienen key configurada."""
    order_env = os.environ.get("JARVIS_LLM_ORDER", "").strip()
    order = [p.strip().lower() for p in order_env.split(",") if p.strip()] or DEFAULT_ORDER
    out: List[Dict[str, Any]] = []
    for name in order:
        pdef = _PROVIDER_DEFS.get(name)
        if not pdef:
            continue
        key = _key_for(pdef)
        if not key:
            continue
        out.append({
            "name": name,
            "key": key,
            "base_url": pdef["base_url"],
            "model": os.environ.get(pdef["model_env"], "").strip() or pdef["default_model"],
        })
    return out


def llm_enabled() -> bool:
    """True si hay al menos un proveedor gratis con key configurada."""
    return bool(_active_providers())


def active_provider_label() -> Optional[str]:
    """Etiqueta legible de la cadena activa, p. ej. 'gemini:gemini-2.5-flash, groq:...'."""
    provs = _active_providers()
    if not provs:
        return None
    return ", ".join(f"{p['name']}:{p['model']}" for p in provs)


# ─── Objetos que imitan la respuesta de Anthropic ────────────────────────────
class _Block:
    __slots__ = ("text", "type")

    def __init__(self, text: str):
        self.text = text
        self.type = "text"


class LLMResponse:
    """Imita el objeto que devuelve anthropic: resp.content[0].text."""

    def __init__(self, text: str, *, provider: str = "", model: str = ""):
        self.content = [_Block(text)]
        self.provider = provider
        self.model = model
        self.stop_reason = "end_turn"


# ─── Conversión de formato Anthropic → OpenAI ────────────────────────────────
def _messages_to_openai(system: Optional[str], messages: Optional[List[dict]]) -> List[dict]:
    out: List[dict] = []
    if system:
        out.append({"role": "system", "content": str(system)})
    for m in (messages or []):
        role = m.get("role", "user")
        content = m.get("content", "")
        # Anthropic permite content como lista de bloques; extraemos el texto.
        if isinstance(content, list):
            parts = []
            for b in content:
                if isinstance(b, dict):
                    parts.append(b.get("text", ""))
                else:
                    parts.append(str(b))
            content = "\n".join(p for p in parts if p)
        out.append({"role": role, "content": content})
    return out


def _call_provider(prov: Dict[str, Any], *, system, messages,
                   max_tokens, temperature, timeout) -> str:
    if httpx is None:
        raise RuntimeError("httpx no disponible")
    url = prov["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": prov["model"],
        "messages": _messages_to_openai(system, messages),
        "max_tokens": int(max_tokens or 1024),
        "temperature": float(temperature if temperature is not None else 0.4),
    }
    headers = {
        "Authorization": f"Bearer {prov['key']}",
        "Content-Type": "application/json",
    }
    # OpenRouter recomienda estos headers (atribución; opcionales pero educados).
    if prov["name"] == "openrouter":
        headers["HTTP-Referer"] = os.environ.get("PORTAL_URL", "https://avanzadigital.digital")
        headers["X-Title"] = "Avanza Digital JARVIS"

    r = httpx.post(url, json=payload, headers=headers, timeout=timeout or DEFAULT_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"respuesta sin 'choices': {str(data)[:200]}")
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    if not text:
        raise RuntimeError("respuesta vacía")
    return text


# ─── Shim con interfaz anthropic.client.messages.create(...) ─────────────────
class _Messages:
    def create(self, *, model=None, max_tokens=1024, system=None,
               messages=None, temperature=None, timeout=None, **kwargs) -> LLMResponse:
        """
        Mismo nombre/firma que anthropic: ignora `model` (usa el del proveedor)
        y recorre la cadena de fallback. `kwargs` absorbe extras (top_p, etc.).
        """
        provs = _active_providers()
        if not provs:
            raise RuntimeError("Sin proveedores de IA configurados (GEMINI/GROQ/OPENROUTER).")
        last_err: Optional[Exception] = None
        for prov in provs:
            try:
                text = _call_provider(
                    prov, system=system, messages=messages,
                    max_tokens=max_tokens, temperature=temperature, timeout=timeout,
                )
                print(f"[JARVIS-LLM] ✅ {prov['name']}/{prov['model']}", file=sys.stderr)
                return LLMResponse(text, provider=prov["name"], model=prov["model"])
            except Exception as e:  # noqa: BLE001 — queremos seguir al siguiente proveedor
                last_err = e
                print(
                    f"[JARVIS-LLM] ⚠️  {prov['name']} falló: "
                    f"{type(e).__name__}: {str(e)[:160]} → siguiente",
                    file=sys.stderr,
                )
                continue
        raise RuntimeError(f"Todos los proveedores de IA fallaron. Último error: {last_err}")


class LLMClient:
    """Imita anthropic.Anthropic(...): expone .messages.create(...)."""

    def __init__(self, *args, **kwargs):  # acepta api_key=... y lo ignora
        self.messages = _Messages()


def get_client() -> Optional[LLMClient]:
    """Devuelve el cliente multi-proveedor, o None si no hay ninguna key."""
    if not llm_enabled():
        return None
    return LLMClient()


# ─── Bootstrap / instalación ─────────────────────────────────────────────────
_installed = False


def install() -> None:
    """
    Activa el ruteo a proveedores gratis. Debe llamarse en main.py ANTES de
    importar los módulos jarvis_* (cada uno lee ANTHROPIC_API_KEY en su import).

    1) Si hay proveedores gratis y ANTHROPIC_API_KEY está vacía, le pone un
       sentinel para que los guards `if not ANTHROPIC_API_KEY` de los módulos
       no corten (el cliente parcheado ignora la key).
    2) Parchea anthropic.Anthropic → LLMClient, para los módulos que instancian
       el cliente directo en vez de usar get_client().

    Idempotente y reversible: sin keys gratis, no hace absolutamente nada
    (el sistema sigue con el comportamiento original de Claude/heurística).
    """
    global _installed
    if _installed:
        return
    if not llm_enabled():
        return  # sin proveedores gratis: no tocamos nada

    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        os.environ["ANTHROPIC_API_KEY"] = _SENTINEL

    try:
        import anthropic
        anthropic.Anthropic = LLMClient  # type: ignore[attr-defined]
    except Exception as e:  # pragma: no cover
        print(f"[JARVIS-LLM] no se pudo parchear anthropic: {e}", file=sys.stderr)

    _installed = True
    print(f"[JARVIS-LLM] activo → {active_provider_label()}", file=sys.stderr)