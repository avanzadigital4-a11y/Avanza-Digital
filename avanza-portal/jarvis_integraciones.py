"""
jarvis_integraciones.py — Conectores nativos de JARVIS.
Sección 10 del Blueprint v2.

INTEGRACIONES:
  ┌─────────────────────────────────────────────────────────────────────┐
  │  CRM                                                                │
  │    HubSpot      → ContactoHubSpot, DealHubSpot, ActividadHubSpot   │
  │    Pipedrive    → PersonaPipedrive, DealPipedrive                   │
  │                                                                     │
  │  Comunicaciones                                                     │
  │    Gmail        → DraftGmail, EnvioGmail                            │
  │    Slack        → MensajeSlack, AlertaSlack                         │
  │    LinkedIn     → DraftLinkedIn (vía extensión Chrome)              │
  │                                                                     │
  │  Productividad                                                      │
  │    Google Calendar → EventoCalendar                                 │
  └─────────────────────────────────────────────────────────────────────┘

DISEÑO:
  - Cada conector implementa BaseConector con métodos estándar
  - Las credenciales vienen de variables de entorno o del perfil del aliado
  - NUNCA se guarda ninguna credencial en el código
  - Todas las funciones devuelven ResultadoIntegracion (ok: bool, data: dict, error: str)
  - Si una integración no está configurada → ok=False, error="no_configurado"
  - El portal puede mostrar el estado de cada integración sin que falle el sistema

AGREGAR AL main.py:
  from jarvis_integraciones import IntegracionManager
  manager = IntegracionManager(aliado_config)
  r = manager.hubspot.crear_contacto(nombre="...", email="...", empresa="...")

USO DESDE jarvis_routes.py:
  from jarvis_integraciones import IntegracionManager, ResultadoIntegracion
  mgr = IntegracionManager.desde_env()
  resultado = mgr.gmail.crear_draft(destinatario=..., asunto=..., cuerpo=...)
"""

from __future__ import annotations

import os
import sys
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any, Literal
from datetime import datetime, timedelta


# ─── RESULTADO ESTÁNDAR ───────────────────────────────────────────────────────

@dataclass
class ResultadoIntegracion:
    ok: bool
    data: dict = field(default_factory=dict)
    error: str = ""
    integracion: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "data": self.data,
            "error": self.error,
            "integracion": self.integracion,
            "timestamp": self.timestamp,
        }


def _ok(integracion: str, data: dict) -> ResultadoIntegracion:
    return ResultadoIntegracion(ok=True, data=data, integracion=integracion)


def _err(integracion: str, error: str) -> ResultadoIntegracion:
    return ResultadoIntegracion(ok=False, error=error, integracion=integracion)


def _no_config(integracion: str) -> ResultadoIntegracion:
    return _err(integracion, "no_configurado")


# ─── BASE ─────────────────────────────────────────────────────────────────────

class BaseConector(ABC):
    nombre: str = "base"

    @abstractmethod
    def esta_configurado(self) -> bool:
        ...

    def estado(self) -> dict:
        return {
            "integracion": self.nombre,
            "configurado": self.esta_configurado(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 1. HUBSPOT
# ─────────────────────────────────────────────────────────────────────────────

class ConectorHubSpot(BaseConector):
    """
    Integración con HubSpot CRM via API privada.
    Requiere: HUBSPOT_ACCESS_TOKEN (OAuth2 o API Key).

    Documentación: https://developers.hubspot.com/docs/api/crm/contacts
    """
    nombre = "hubspot"

    def __init__(self, access_token: str = ""):
        self.token = access_token or os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
        self._base = "https://api.hubapi.com"

    def esta_configurado(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict = None) -> tuple[bool, dict]:
        """Hace una request HTTP a HubSpot. Devuelve (ok, data)."""
        if not self.esta_configurado():
            return False, {"error": "no_configurado"}
        try:
            import urllib.request
            import urllib.error
            url = f"{self._base}{path}"
            data_bytes = json.dumps(body).encode() if body else None
            req = urllib.request.Request(url, data=data_bytes, headers=self._headers(), method=method)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, json.loads(resp.read())
        except Exception as e:
            print(f"[HUBSPOT] Error: {e}", file=sys.stderr)
            return False, {"error": str(e)}

    # ── Contactos ─────────────────────────────────────────────────────────────

    def buscar_contacto(self, email: str) -> ResultadoIntegracion:
        """Busca un contacto por email. Devuelve el contacto si existe."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body = {
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
            "properties": ["firstname", "lastname", "company", "phone", "email", "hs_object_id"],
            "limit": 1,
        }
        ok, data = self._request("POST", "/crm/v3/objects/contacts/search", body)
        if ok and data.get("total", 0) > 0:
            contacto = data["results"][0]
            return _ok(self.nombre, {"encontrado": True, "contacto": contacto})
        elif ok:
            return _ok(self.nombre, {"encontrado": False})
        return _err(self.nombre, data.get("error", "error_busqueda"))

    def crear_contacto(
        self,
        nombre: str,
        apellido: str = "",
        email: str = "",
        empresa: str = "",
        telefono: str = "",
        cargo: str = "",
        nota: str = "",
    ) -> ResultadoIntegracion:
        """Crea un contacto en HubSpot."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        properties = {
            "firstname": nombre,
            "lastname": apellido,
            "email": email,
            "company": empresa,
            "phone": telefono,
            "jobtitle": cargo,
        }
        if nota:
            properties["notes_last_contacted"] = nota

        # Filtrar campos vacíos
        properties = {k: v for k, v in properties.items() if v}

        ok, data = self._request("POST", "/crm/v3/objects/contacts", {"properties": properties})
        if ok:
            return _ok(self.nombre, {"contacto_id": data.get("id"), "contacto": data})
        return _err(self.nombre, data.get("error", "error_creacion"))

    def actualizar_contacto(self, contacto_id: str, campos: dict) -> ResultadoIntegracion:
        """Actualiza campos de un contacto existente."""
        if not self.esta_configurado():
            return _no_config(self.nombre)
        ok, data = self._request("PATCH", f"/crm/v3/objects/contacts/{contacto_id}", {"properties": campos})
        if ok:
            return _ok(self.nombre, {"actualizado": True, "contacto_id": contacto_id})
        return _err(self.nombre, data.get("error", "error_actualizacion"))

    # ── Deals ─────────────────────────────────────────────────────────────────

    def crear_deal(
        self,
        nombre_deal: str,
        etapa: str = "appointmentscheduled",
        valor_usd: float = 0,
        contacto_id: str = "",
        empresa_id: str = "",
        fecha_cierre: Optional[str] = None,
        nota_interna: str = "",
    ) -> ResultadoIntegracion:
        """
        Crea un deal en HubSpot.
        etapa: appointmentscheduled | qualifiedtobuy | presentationscheduled |
               decisionmakerboughtin | contractsent | closedwon | closedlost
        """
        if not self.esta_configurado():
            return _no_config(self.nombre)

        if not fecha_cierre:
            fecha_cierre = (datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d")

        properties = {
            "dealname": nombre_deal,
            "dealstage": etapa,
            "amount": str(valor_usd) if valor_usd else "0",
            "closedate": fecha_cierre,
        }
        if nota_interna:
            properties["description"] = nota_interna

        ok, data = self._request("POST", "/crm/v3/objects/deals", {"properties": properties})
        if not ok:
            return _err(self.nombre, data.get("error", "error_deal"))

        deal_id = data.get("id")

        # Asociar con contacto si se proveyó
        if deal_id and contacto_id:
            self._request(
                "PUT",
                f"/crm/v4/objects/deals/{deal_id}/associations/contacts/{contacto_id}/deal_to_contact",
                {}
            )

        return _ok(self.nombre, {"deal_id": deal_id, "deal": data})

    # ── Actividades / Notas ────────────────────────────────────────────────────

    def registrar_actividad(
        self,
        contacto_id: str,
        tipo: Literal["email", "llamada", "reunion", "nota"],
        resumen: str,
        cuerpo: str = "",
    ) -> ResultadoIntegracion:
        """Registra una actividad asociada a un contacto."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        tipo_map = {
            "email":   "emails",
            "llamada": "calls",
            "reunion": "meetings",
            "nota":    "notes",
        }
        endpoint_tipo = tipo_map.get(tipo, "notes")

        properties: dict = {}
        if tipo == "nota":
            properties = {"hs_note_body": f"{resumen}\n\n{cuerpo}".strip()}
        elif tipo == "email":
            properties = {"hs_email_subject": resumen, "hs_email_text": cuerpo}
        elif tipo == "llamada":
            properties = {"hs_call_title": resumen, "hs_call_body": cuerpo}
        elif tipo == "reunion":
            properties = {"hs_meeting_title": resumen, "hs_meeting_body": cuerpo}

        ok, data = self._request("POST", f"/crm/v3/objects/{endpoint_tipo}", {"properties": properties})
        if not ok:
            return _err(self.nombre, data.get("error", "error_actividad"))

        obj_id = data.get("id")
        # Asociar con contacto
        if obj_id:
            self._request(
                "PUT",
                f"/crm/v4/objects/{endpoint_tipo}/{obj_id}/associations/contacts/{contacto_id}/{endpoint_tipo[:-1]}_to_contact",
                {}
            )

        return _ok(self.nombre, {"actividad_id": obj_id, "tipo": tipo})

    def listar_deals(self, limit: int = 20) -> ResultadoIntegracion:
        """Lista los últimos deals."""
        if not self.esta_configurado():
            return _no_config(self.nombre)
        ok, data = self._request(
            "GET",
            f"/crm/v3/objects/deals?limit={limit}&properties=dealname,dealstage,amount,closedate",
        )
        if ok:
            return _ok(self.nombre, {"deals": data.get("results", [])})
        return _err(self.nombre, data.get("error", "error_listado"))


# ─────────────────────────────────────────────────────────────────────────────
# 2. PIPEDRIVE
# ─────────────────────────────────────────────────────────────────────────────

class ConectorPipedrive(BaseConector):
    """
    Integración con Pipedrive CRM via API v1.
    Requiere: PIPEDRIVE_API_TOKEN, PIPEDRIVE_DOMAIN (ej: "mi-empresa")
    """
    nombre = "pipedrive"

    def __init__(self, api_token: str = "", domain: str = ""):
        self.token  = api_token or os.environ.get("PIPEDRIVE_API_TOKEN", "")
        self.domain = domain    or os.environ.get("PIPEDRIVE_DOMAIN", "api")
        self._base  = f"https://{self.domain}.pipedrive.com/v1"

    def esta_configurado(self) -> bool:
        return bool(self.token)

    def _request(self, method: str, path: str, body: dict = None) -> tuple[bool, dict]:
        if not self.esta_configurado():
            return False, {"error": "no_configurado"}
        try:
            import urllib.request
            sep = "&" if "?" in path else "?"
            url = f"{self._base}{path}{sep}api_token={self.token}"
            headers = {"Content-Type": "application/json"}
            data_bytes = json.dumps(body).encode() if body else None
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, json.loads(resp.read())
        except Exception as e:
            print(f"[PIPEDRIVE] Error: {e}", file=sys.stderr)
            return False, {"error": str(e)}

    def crear_persona(
        self,
        nombre: str,
        email: str = "",
        telefono: str = "",
        empresa_id: int = None,
    ) -> ResultadoIntegracion:
        """Crea una persona (contacto) en Pipedrive."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body: dict = {"name": nombre}
        if email:    body["email"]  = [{"value": email, "primary": True}]
        if telefono: body["phone"]  = [{"value": telefono, "primary": True}]
        if empresa_id: body["org_id"] = empresa_id

        ok, data = self._request("POST", "/persons", body)
        if ok and data.get("success"):
            return _ok(self.nombre, {"persona_id": data["data"]["id"], "persona": data["data"]})
        return _err(self.nombre, data.get("error", "error_creacion"))

    def crear_empresa(self, nombre: str, sector: str = "") -> ResultadoIntegracion:
        """Crea una organización en Pipedrive."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body = {"name": nombre}
        ok, data = self._request("POST", "/organizations", body)
        if ok and data.get("success"):
            return _ok(self.nombre, {"empresa_id": data["data"]["id"]})
        return _err(self.nombre, data.get("error", "error_creacion_empresa"))

    def crear_deal(
        self,
        titulo: str,
        persona_id: int = None,
        empresa_id: int = None,
        valor: float = 0,
        moneda: str = "USD",
        etapa_id: int = None,
        fecha_cierre_esperada: str = "",
    ) -> ResultadoIntegracion:
        """Crea un deal (negocio) en Pipedrive."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body: dict = {"title": titulo, "currency": moneda}
        if persona_id:   body["person_id"] = persona_id
        if empresa_id:   body["org_id"]    = empresa_id
        if valor:        body["value"]      = valor
        if etapa_id:     body["stage_id"]  = etapa_id
        if fecha_cierre_esperada:
            body["expected_close_date"] = fecha_cierre_esperada

        ok, data = self._request("POST", "/deals", body)
        if ok and data.get("success"):
            return _ok(self.nombre, {"deal_id": data["data"]["id"], "deal": data["data"]})
        return _err(self.nombre, data.get("error", "error_deal"))

    def agregar_nota(self, contenido: str, deal_id: int = None, persona_id: int = None) -> ResultadoIntegracion:
        """Agrega una nota a un deal o persona."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body: dict = {"content": contenido}
        if deal_id:    body["deal_id"]   = deal_id
        if persona_id: body["person_id"] = persona_id

        ok, data = self._request("POST", "/notes", body)
        if ok and data.get("success"):
            return _ok(self.nombre, {"nota_id": data["data"]["id"]})
        return _err(self.nombre, data.get("error", "error_nota"))

    def listar_deals(self, etapa_id: int = None, limit: int = 20) -> ResultadoIntegracion:
        """Lista deals, opcionalmente filtrados por etapa."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        path = f"/deals?limit={limit}"
        if etapa_id:
            path += f"&stage_id={etapa_id}"

        ok, data = self._request("GET", path)
        if ok and data.get("success"):
            return _ok(self.nombre, {"deals": data.get("data", [])})
        return _err(self.nombre, data.get("error", "error_listado"))

    def actualizar_etapa_deal(self, deal_id: int, etapa_id: int) -> ResultadoIntegracion:
        """Mueve un deal a otra etapa del pipeline."""
        if not self.esta_configurado():
            return _no_config(self.nombre)
        ok, data = self._request("PUT", f"/deals/{deal_id}", {"stage_id": etapa_id})
        if ok and data.get("success"):
            return _ok(self.nombre, {"deal_id": deal_id, "etapa_id": etapa_id})
        return _err(self.nombre, data.get("error", "error_actualizacion"))


# ─────────────────────────────────────────────────────────────────────────────
# 3. GMAIL
# ─────────────────────────────────────────────────────────────────────────────

class ConectorGmail(BaseConector):
    """
    Integración con Gmail via Google API (OAuth2).
    Requiere: GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN

    Para obtener el refresh_token por primera vez:
    1. Crear proyecto en console.cloud.google.com
    2. Activar Gmail API
    3. Crear OAuth2 credentials
    4. Correr el flujo de autorización una vez (ver get_refresh_token())
    """
    nombre = "gmail"

    def __init__(
        self,
        client_id: str = "",
        client_secret: str = "",
        refresh_token: str = "",
    ):
        self.client_id     = client_id     or os.environ.get("GMAIL_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("GMAIL_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or os.environ.get("GMAIL_REFRESH_TOKEN", "")
        self._access_token: str = ""
        self._token_expires: float = 0.0

    def esta_configurado(self) -> bool:
        return all([self.client_id, self.client_secret, self.refresh_token])

    def _obtener_access_token(self) -> Optional[str]:
        """Renueva el access token usando el refresh token."""
        if self._access_token and time.time() < self._token_expires:
            return self._access_token

        try:
            import urllib.request
            import urllib.parse

            body = urllib.parse.urlencode({
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type":    "refresh_token",
            }).encode()

            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                self._access_token  = data.get("access_token", "")
                self._token_expires = time.time() + data.get("expires_in", 3600) - 60
                return self._access_token
        except Exception as e:
            print(f"[GMAIL] Error al obtener access token: {e}", file=sys.stderr)
            return None

    def _gmail_request(self, method: str, path: str, body: dict = None) -> tuple[bool, dict]:
        if not self.esta_configurado():
            return False, {"error": "no_configurado"}

        token = self._obtener_access_token()
        if not token:
            return False, {"error": "token_invalido"}

        try:
            import urllib.request
            url = f"https://gmail.googleapis.com/gmail/v1{path}"
            data_bytes = json.dumps(body).encode() if body else None
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return True, json.loads(resp.read())
        except Exception as e:
            print(f"[GMAIL] Error en request: {e}", file=sys.stderr)
            return False, {"error": str(e)}

    def _construir_mime(
        self,
        destinatario: str,
        asunto: str,
        cuerpo_texto: str,
        cuerpo_html: str = "",
        cc: str = "",
        reply_to: str = "",
    ) -> str:
        """Construye el email en formato MIME base64."""
        import base64
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart

        if cuerpo_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(cuerpo_texto, "plain", "utf-8"))
            msg.attach(MIMEText(cuerpo_html, "html", "utf-8"))
        else:
            msg = MIMEText(cuerpo_texto, "plain", "utf-8")

        msg["To"] = destinatario
        msg["Subject"] = asunto
        if cc:       msg["Cc"] = cc
        if reply_to: msg["Reply-To"] = reply_to

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        return raw

    def crear_draft(
        self,
        destinatario: str,
        asunto: str,
        cuerpo: str,
        cuerpo_html: str = "",
        cc: str = "",
    ) -> ResultadoIntegracion:
        """
        Crea un borrador en Gmail (NO envía).
        El aliado lo revisa y envía manualmente desde Gmail.
        """
        if not self.esta_configurado():
            return _no_config(self.nombre)

        raw = self._construir_mime(destinatario, asunto, cuerpo, cuerpo_html, cc)
        body = {"message": {"raw": raw}}

        ok, data = self._gmail_request("POST", "/users/me/drafts", body)
        if ok:
            draft_id = data.get("id", "")
            return _ok(self.nombre, {
                "draft_id": draft_id,
                "draft_url": f"https://mail.google.com/mail/#drafts/{draft_id}",
                "mensaje": "Borrador creado en Gmail. Revisalo antes de enviar.",
            })
        return _err(self.nombre, data.get("error", "error_draft"))

    def enviar_email(
        self,
        destinatario: str,
        asunto: str,
        cuerpo: str,
        cuerpo_html: str = "",
        cc: str = "",
        thread_id: str = "",
    ) -> ResultadoIntegracion:
        """
        Envía un email directamente.
        ADVERTENCIA: Úsalo solo si el aliado lo habilitó explícitamente.
        Siempre preferir crear_draft() para que el aliado revise.
        """
        if not self.esta_configurado():
            return _no_config(self.nombre)

        raw = self._construir_mime(destinatario, asunto, cuerpo, cuerpo_html, cc)
        body: dict = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id

        ok, data = self._gmail_request("POST", "/users/me/messages/send", body)
        if ok:
            return _ok(self.nombre, {
                "message_id": data.get("id"),
                "thread_id": data.get("threadId"),
                "enviado": True,
            })
        return _err(self.nombre, data.get("error", "error_envio"))

    def listar_threads_recientes(self, max_results: int = 10, query: str = "") -> ResultadoIntegracion:
        """Lista threads recientes del inbox."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        params = f"maxResults={max_results}"
        if query:
            import urllib.parse
            params += f"&q={urllib.parse.quote(query)}"

        ok, data = self._gmail_request("GET", f"/users/me/threads?{params}")
        if ok:
            return _ok(self.nombre, {"threads": data.get("threads", [])})
        return _err(self.nombre, data.get("error", "error_listado"))


# ─────────────────────────────────────────────────────────────────────────────
# 4. GOOGLE CALENDAR
# ─────────────────────────────────────────────────────────────────────────────

class ConectorGoogleCalendar(BaseConector):
    """
    Integración con Google Calendar via API (OAuth2).
    Comparte las credenciales de Gmail (mismo scope: calendar).
    Requiere: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
    """
    nombre = "google_calendar"

    def __init__(self, client_id: str = "", client_secret: str = "", refresh_token: str = ""):
        # Intentar usar las de Gmail primero, luego las propias
        self.client_id     = client_id     or os.environ.get("GOOGLE_CLIENT_ID",     "") or os.environ.get("GMAIL_CLIENT_ID",     "")
        self.client_secret = client_secret or os.environ.get("GOOGLE_CLIENT_SECRET", "") or os.environ.get("GMAIL_CLIENT_SECRET", "")
        self.refresh_token = refresh_token or os.environ.get("GOOGLE_REFRESH_TOKEN", "") or os.environ.get("GMAIL_REFRESH_TOKEN", "")
        self._access_token: str  = ""
        self._token_expires: float = 0.0

    def esta_configurado(self) -> bool:
        return all([self.client_id, self.client_secret, self.refresh_token])

    def _obtener_access_token(self) -> Optional[str]:
        if self._access_token and time.time() < self._token_expires:
            return self._access_token
        try:
            import urllib.request, urllib.parse
            body = urllib.parse.urlencode({
                "client_id":     self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type":    "refresh_token",
            }).encode()
            req = urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                self._access_token  = data.get("access_token", "")
                self._token_expires = time.time() + data.get("expires_in", 3600) - 60
                return self._access_token
        except Exception as e:
            print(f"[CALENDAR] Token error: {e}", file=sys.stderr)
            return None

    def _cal_request(self, method: str, path: str, body: dict = None) -> tuple[bool, dict]:
        token = self._obtener_access_token()
        if not token:
            return False, {"error": "token_invalido"}
        try:
            import urllib.request
            url = f"https://www.googleapis.com/calendar/v3{path}"
            data_bytes = json.dumps(body).encode() if body else None
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return True, json.loads(resp.read())
        except Exception as e:
            print(f"[CALENDAR] Request error: {e}", file=sys.stderr)
            return False, {"error": str(e)}

    def crear_evento(
        self,
        titulo: str,
        fecha_inicio: str,        # ISO 8601: "2026-06-15T10:00:00-03:00"
        fecha_fin: str,            # ISO 8601: "2026-06-15T11:00:00-03:00"
        descripcion: str = "",
        invitados: list[str] = None,
        lugar: str = "",
        recordatorio_minutos: int = 30,
        calendar_id: str = "primary",
    ) -> ResultadoIntegracion:
        """Crea un evento en Google Calendar."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        body: dict = {
            "summary": titulo,
            "start":   {"dateTime": fecha_inicio, "timeZone": "America/Argentina/Buenos_Aires"},
            "end":     {"dateTime": fecha_fin,    "timeZone": "America/Argentina/Buenos_Aires"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": recordatorio_minutos},
                    {"method": "email", "minutes": recordatorio_minutos * 2},
                ],
            },
        }
        if descripcion: body["description"] = descripcion
        if lugar:       body["location"]    = lugar
        if invitados:
            body["attendees"] = [{"email": e} for e in invitados if e]

        ok, data = self._cal_request("POST", f"/calendars/{calendar_id}/events", body)
        if ok:
            return _ok(self.nombre, {
                "evento_id":  data.get("id"),
                "evento_url": data.get("htmlLink", ""),
                "titulo":     titulo,
            })
        return _err(self.nombre, data.get("error", "error_evento"))

    def crear_recordatorio(
        self,
        titulo: str,
        fecha_hora: str,           # ISO 8601
        descripcion: str = "",
        calendar_id: str = "primary",
    ) -> ResultadoIntegracion:
        """
        Crea un recordatorio de 15 minutos (bloque breve en el calendario).
        Útil para seguimientos de leads.
        """
        dt_inicio = fecha_hora  # ISO 8601
        # Agregar 15 minutos como bloque mínimo
        try:
            from datetime import datetime, timedelta
            dt = datetime.fromisoformat(fecha_hora.replace("Z", "+00:00"))
            dt_fin = (dt + timedelta(minutes=15)).isoformat()
        except Exception:
            dt_fin = fecha_hora

        return self.crear_evento(
            titulo=f"⏰ {titulo}",
            fecha_inicio=dt_inicio,
            fecha_fin=dt_fin,
            descripcion=descripcion,
            recordatorio_minutos=10,
            calendar_id=calendar_id,
        )

    def listar_eventos_proximos(self, dias: int = 7, calendar_id: str = "primary") -> ResultadoIntegracion:
        """Lista los próximos N días de eventos."""
        if not self.esta_configurado():
            return _no_config(self.nombre)

        ahora = datetime.utcnow().isoformat() + "Z"
        hasta = (datetime.utcnow() + timedelta(days=dias)).isoformat() + "Z"
        import urllib.parse
        params = urllib.parse.urlencode({
            "timeMin": ahora,
            "timeMax": hasta,
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": 20,
        })
        ok, data = self._cal_request("GET", f"/calendars/{calendar_id}/events?{params}")
        if ok:
            return _ok(self.nombre, {"eventos": data.get("items", [])})
        return _err(self.nombre, data.get("error", "error_listado"))


# ─────────────────────────────────────────────────────────────────────────────
# 5. SLACK
# ─────────────────────────────────────────────────────────────────────────────

class ConectorSlack(BaseConector):
    """
    Integración con Slack via Incoming Webhooks o Bot Token.
    Requiere: SLACK_BOT_TOKEN (xoxb-...) o SLACK_WEBHOOK_URL
    """
    nombre = "slack"

    def __init__(self, bot_token: str = "", webhook_url: str = ""):
        self.bot_token   = bot_token   or os.environ.get("SLACK_BOT_TOKEN", "")
        self.webhook_url = webhook_url or os.environ.get("SLACK_WEBHOOK_URL", "")

    def esta_configurado(self) -> bool:
        return bool(self.bot_token or self.webhook_url)

    def _post_webhook(self, payload: dict) -> tuple[bool, dict]:
        """Envía un mensaje via Incoming Webhook."""
        try:
            import urllib.request
            data_bytes = json.dumps(payload).encode()
            req = urllib.request.Request(
                self.webhook_url,
                data=data_bytes,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode()
                return body == "ok", {"response": body}
        except Exception as e:
            return False, {"error": str(e)}

    def _post_api(self, endpoint: str, payload: dict) -> tuple[bool, dict]:
        """Usa el Bot Token para llamar a la Web API."""
        try:
            import urllib.request
            data_bytes = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"https://slack.com/api/{endpoint}",
                data=data_bytes,
                headers={
                    "Authorization": f"Bearer {self.bot_token}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data.get("ok", False), data
        except Exception as e:
            return False, {"error": str(e)}

    def enviar_mensaje(
        self,
        canal: str,
        texto: str,
        bloques: Optional[list[dict]] = None,
    ) -> ResultadoIntegracion:
        """
        Envía un mensaje a un canal o DM.
        canal: "#jarvis-alertas", "@usuario", "C01234567" (channel ID)
        """
        if not self.esta_configurado():
            return _no_config(self.nombre)

        payload: dict = {"channel": canal, "text": texto}
        if bloques:
            payload["blocks"] = bloques

        if self.bot_token:
            ok, data = self._post_api("chat.postMessage", payload)
        elif self.webhook_url:
            ok, data = self._post_webhook({"text": texto})
        else:
            return _no_config(self.nombre)

        if ok:
            return _ok(self.nombre, {"ts": data.get("ts", ""), "canal": canal})
        return _err(self.nombre, data.get("error", "error_envio"))

    def alerta_jarvis(
        self,
        canal: str,
        titulo: str,
        mensaje: str,
        nivel: Literal["info", "warning", "critical"] = "info",
        accion_url: str = "",
    ) -> ResultadoIntegracion:
        """
        Envía una alerta formateada de JARVIS a un canal de Slack.
        Usa Block Kit para un diseño limpio.
        """
        emoji_map = {"info": "ℹ️", "warning": "⚠️", "critical": "🔴"}
        color_map = {"info": "#3b82f6", "warning": "#f59e0b", "critical": "#ef4444"}

        bloques = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{emoji_map[nivel]} JARVIS — {titulo}*\n{mensaje}",
                },
            }
        ]

        if accion_url:
            bloques.append({
                "type": "actions",
                "elements": [{
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Ver en el portal"},
                    "url": accion_url,
                    "style": "primary" if nivel == "critical" else "default",
                }]
            })

        return self.enviar_mensaje(canal, texto=f"{titulo}: {mensaje}", bloques=bloques)

    def listar_canales(self) -> ResultadoIntegracion:
        """Lista los canales del workspace."""
        if not self.bot_token:
            return _no_config(self.nombre)
        ok, data = self._post_api("conversations.list", {"limit": 50, "exclude_archived": True})
        if ok:
            canales = [{"id": c["id"], "nombre": c["name"]} for c in data.get("channels", [])]
            return _ok(self.nombre, {"canales": canales})
        return _err(self.nombre, data.get("error", "error_listado"))


# ─────────────────────────────────────────────────────────────────────────────
# 6. LINKEDIN (via extensión Chrome)
# ─────────────────────────────────────────────────────────────────────────────

class ConectorLinkedIn(BaseConector):
    """
    Integración con LinkedIn.

    LinkedIn no tiene una API pública para enviar mensajes directos.
    La estrategia es:
      1. JARVIS genera el borrador del mensaje
      2. El portal lo muestra con botón "Abrir en LinkedIn"
      3. Una extensión Chrome (futura) puede hacer el envío automático

    Por ahora: generación de drafts + construcción de URLs de contacto.
    """
    nombre = "linkedin"

    def __init__(self):
        # No requiere token por ahora (modo draft)
        self.linkedin_access_token = os.environ.get("LINKEDIN_ACCESS_TOKEN", "")

    def esta_configurado(self) -> bool:
        # Siempre disponible en modo draft
        return True

    def generar_draft_mensaje(
        self,
        nombre_prospecto: str,
        empresa_prospecto: str,
        cargo_prospecto: str,
        contexto: str,
        objetivo: Literal["primer_contacto", "seguimiento", "referido", "comentario"] = "primer_contacto",
    ) -> ResultadoIntegracion:
        """
        Genera un borrador de mensaje de LinkedIn para que el aliado lo envíe manualmente.
        Este draft se pasa a JARVIS (jarvis.py) para la redacción contextual.
        """
        # Templates base por objetivo
        templates = {
            "primer_contacto": (
                f"Hola {nombre_prospecto}, veo que estás en {empresa_prospecto}. "
                f"Trabajo con empresas similares en la región y tengo algo puntual "
                f"que podría servirles. ¿Le dedicaría 10 minutos?"
            ),
            "seguimiento": (
                f"Hola {nombre_prospecto}, sigo el trabajo que hacen en {empresa_prospecto}. "
                f"Quería compartirte algo específico que puede ser relevante para tu equipo."
            ),
            "referido": (
                f"Hola {nombre_prospecto}, {contexto}. "
                f"Me comentaron que podrías estar interesado/a en lo que hacemos."
            ),
            "comentario": (
                f"Excelente punto, {nombre_prospecto}. "
                f"En nuestra experiencia con empresas del sector, esto impacta directamente en..."
            ),
        }

        draft = templates.get(objetivo, templates["primer_contacto"])

        return _ok(self.nombre, {
            "draft": draft,
            "caracteres": len(draft),
            "limite_linkedin": 300,
            "objetivo": objetivo,
            "url_perfil_search": (
                f"https://www.linkedin.com/search/results/people/?keywords="
                f"{nombre_prospecto.replace(' ', '%20')}%20{empresa_prospecto.replace(' ', '%20')}"
            ),
            "instruccion": "Revisá y personalizá el mensaje antes de enviarlo desde LinkedIn.",
        })

    def construir_url_perfil(self, nombre: str, empresa: str = "") -> str:
        """Construye una URL de búsqueda en LinkedIn."""
        import urllib.parse
        query = f"{nombre} {empresa}".strip()
        return f"https://www.linkedin.com/search/results/people/?keywords={urllib.parse.quote(query)}"

    def generar_comentario_publicacion(
        self,
        texto_publicacion: str,
        autor: str,
        empresa_autor: str,
        nombre_aliado: str,
        producto_avanza: str = "sistemas web industriales",
    ) -> ResultadoIntegracion:
        """
        Genera un comentario estratégico para una publicación de un prospecto en LinkedIn.
        El aliado lo copia y pega manualmente.
        """
        # Template de comentario que agrega valor sin ser publicitario
        comentario = (
            f"Excelente observación, {autor}. "
            f"En los proyectos que desarrollamos con empresas del sector, "
            f"justamente este punto marca la diferencia entre digitalizar y digitalizar bien. "
            f"Me gustaría compartirte un caso puntual si te parece."
        )

        return _ok(self.nombre, {
            "comentario": comentario,
            "caracteres": len(comentario),
            "instruccion": f"Comentá en la publicación de {autor} en LinkedIn y mencioná el caso concreto.",
        })


# ─────────────────────────────────────────────────────────────────────────────
# MANAGER CENTRAL
# ─────────────────────────────────────────────────────────────────────────────

class IntegracionManager:
    """
    Punto de entrada único para todas las integraciones de JARVIS.

    Uso desde jarvis_routes.py:
        mgr = IntegracionManager.desde_env()
        r   = mgr.hubspot.crear_contacto(...)
        r   = mgr.gmail.crear_draft(...)

    Uso con config personalizada (por aliado):
        mgr = IntegracionManager(
            hubspot_token="...",
            gmail_refresh_token="...",
            ...
        )
    """

    def __init__(
        self,
        hubspot_token: str = "",
        pipedrive_token: str = "",
        pipedrive_domain: str = "",
        gmail_client_id: str = "",
        gmail_client_secret: str = "",
        gmail_refresh_token: str = "",
        google_client_id: str = "",
        google_client_secret: str = "",
        google_refresh_token: str = "",
        slack_bot_token: str = "",
        slack_webhook_url: str = "",
    ):
        self.hubspot  = ConectorHubSpot(hubspot_token)
        self.pipedrive = ConectorPipedrive(pipedrive_token, pipedrive_domain)
        self.gmail    = ConectorGmail(gmail_client_id, gmail_client_secret, gmail_refresh_token)
        self.calendar = ConectorGoogleCalendar(google_client_id or gmail_client_id,
                                               google_client_secret or gmail_client_secret,
                                               google_refresh_token or gmail_refresh_token)
        self.slack    = ConectorSlack(slack_bot_token, slack_webhook_url)
        self.linkedin = ConectorLinkedIn()

    @classmethod
    def desde_env(cls) -> "IntegracionManager":
        """Carga todas las credenciales desde variables de entorno."""
        return cls()  # El constructor ya lee los env vars

    def estado_todas(self) -> dict:
        """Retorna el estado de configuración de todas las integraciones."""
        return {
            integ.nombre: integ.estado()
            for integ in [self.hubspot, self.pipedrive, self.gmail,
                         self.calendar, self.slack, self.linkedin]
        }

    def integraciones_activas(self) -> list[str]:
        """Retorna la lista de integraciones configuradas."""
        return [
            nombre
            for nombre, estado in self.estado_todas().items()
            if estado["configurado"]
        ]

    def guardar_lead_en_crm(
        self,
        nombre: str,
        email: str,
        empresa: str,
        cargo: str = "",
        telefono: str = "",
        nota: str = "",
        valor_deal_usd: float = 0,
    ) -> dict[str, ResultadoIntegracion]:
        """
        Guarda un lead en todos los CRMs configurados simultáneamente.
        Retorna un dict con el resultado de cada integración.
        """
        resultados: dict[str, ResultadoIntegracion] = {}

        if self.hubspot.esta_configurado():
            r_hs = self.hubspot.crear_contacto(nombre, "", email, empresa, telefono, cargo, nota)
            resultados["hubspot"] = r_hs
            if r_hs.ok and valor_deal_usd:
                contacto_id = r_hs.data.get("contacto_id", "")
                r_deal = self.hubspot.crear_deal(
                    nombre_deal=f"{nombre} — {empresa}",
                    valor_usd=valor_deal_usd,
                    contacto_id=contacto_id,
                    nota_interna=nota,
                )
                resultados["hubspot_deal"] = r_deal

        if self.pipedrive.esta_configurado():
            r_pd = self.pipedrive.crear_persona(nombre, email, telefono)
            resultados["pipedrive"] = r_pd
            if r_pd.ok:
                persona_id = r_pd.data.get("persona_id")
                if persona_id:
                    r_deal = self.pipedrive.crear_deal(
                        titulo=f"{nombre} — {empresa}",
                        persona_id=persona_id,
                        valor=valor_deal_usd,
                    )
                    resultados["pipedrive_deal"] = r_deal

        return resultados

    def notificar_equipo(
        self,
        titulo: str,
        mensaje: str,
        nivel: Literal["info", "warning", "critical"] = "info",
        canal_slack: str = "#jarvis-alertas",
    ) -> ResultadoIntegracion:
        """Envía una notificación al equipo via Slack."""
        if self.slack.esta_configurado():
            return self.slack.alerta_jarvis(canal_slack, titulo, mensaje, nivel)
        return _err("slack", "no_configurado")


# ─── REGISTRO DE RUTAS (opcional, para jarvis_routes.py) ─────────────────────

def register_integration_routes(app, get_db_func, auth_dep):
    """
    Registra endpoints de integraciones en la app FastAPI.

    GET  /jarvis/integraciones/estado    → Estado de todas las integraciones
    POST /jarvis/integraciones/crm/lead  → Guarda lead en CRM(s) configurado(s)
    POST /jarvis/integraciones/gmail/draft → Crea borrador en Gmail
    POST /jarvis/integraciones/calendar/evento → Crea evento en Calendar
    POST /jarvis/integraciones/slack/alerta   → Envía alerta a Slack
    """
    from fastapi import Depends
    from fastapi.responses import JSONResponse

    mgr = IntegracionManager.desde_env()

    @app.get("/jarvis/integraciones/estado")
    def integraciones_estado(aliado=Depends(auth_dep)):
        return JSONResponse(content={
            "integraciones": mgr.estado_todas(),
            "activas": mgr.integraciones_activas(),
        })

    @app.post("/jarvis/integraciones/crm/lead")
    async def guardar_lead_crm(request, aliado=Depends(auth_dep)):
        body = await request.json()
        resultados = mgr.guardar_lead_en_crm(
            nombre=body.get("nombre", ""),
            email=body.get("email", ""),
            empresa=body.get("empresa", ""),
            cargo=body.get("cargo", ""),
            telefono=body.get("telefono", ""),
            nota=body.get("nota", ""),
            valor_deal_usd=float(body.get("valor_deal_usd", 0)),
        )
        return JSONResponse(content={k: v.to_dict() for k, v in resultados.items()})

    @app.post("/jarvis/integraciones/gmail/draft")
    async def gmail_draft(request, aliado=Depends(auth_dep)):
        body = await request.json()
        resultado = mgr.gmail.crear_draft(
            destinatario=body.get("destinatario", ""),
            asunto=body.get("asunto", ""),
            cuerpo=body.get("cuerpo", ""),
        )
        return JSONResponse(content=resultado.to_dict())

    @app.post("/jarvis/integraciones/calendar/evento")
    async def calendar_evento(request, aliado=Depends(auth_dep)):
        body = await request.json()
        resultado = mgr.calendar.crear_evento(
            titulo=body.get("titulo", ""),
            fecha_inicio=body.get("fecha_inicio", ""),
            fecha_fin=body.get("fecha_fin", ""),
            descripcion=body.get("descripcion", ""),
            invitados=body.get("invitados", []),
        )
        return JSONResponse(content=resultado.to_dict())

    @app.post("/jarvis/integraciones/slack/alerta")
    async def slack_alerta(request, aliado=Depends(auth_dep)):
        body = await request.json()
        resultado = mgr.slack.alerta_jarvis(
            canal=body.get("canal", "#jarvis-alertas"),
            titulo=body.get("titulo", ""),
            mensaje=body.get("mensaje", ""),
            nivel=body.get("nivel", "info"),
        )
        return JSONResponse(content=resultado.to_dict())


# ─── SELF-TEST ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=== TEST: Estado de integraciones (sin credenciales) ===\n")
    mgr = IntegracionManager.desde_env()
    estado = mgr.estado_todas()
    for nombre, data in estado.items():
        icono = "✅" if data["configurado"] else "❌"
        print(f"{icono} {nombre}")

    print("\n=== TEST: LinkedIn (siempre disponible) ===\n")
    r = mgr.linkedin.generar_draft_mensaje(
        nombre_prospecto="Ing. Martínez",
        empresa_prospecto="MetalPro SRL",
        cargo_prospecto="Gerente de Producción",
        contexto="sector metalúrgico norte bonaerense",
        objetivo="primer_contacto",
    )
    print("Draft:", r.data.get("draft"))
    print("Búsqueda:", r.data.get("url_perfil_search"))

    print("\n=== TEST: Momento óptimo (módulo emocional) ===\n")
    from jarvis_emocional import momento_optimo_contacto
    for sector in ["metalurgica", "agro", "logistica", "default"]:
        m = momento_optimo_contacto(sector)
        print(f"[{sector}] {m['dia_semana']} {m['hora_inicio']}-{m['hora_fin']}: {m['razon']}")