# -*- coding: utf-8 -*-
"""
jarvis_contratos_routes.py — Rutas para generar el contrato en PDF.

Se registra igual que jarvis_routes. En main.py, junto a donde registrás
jarvis_routes, agregá:

    import jarvis_contratos_routes
    jarvis_contratos_routes.register(app, get_db, auth_dep)   # mismos args que jarvis_routes

Endpoints que agrega:
    POST /ventas/{venta_id}/contrato   → genera el PDF a partir de una Venta + datos fiscales
    POST /contratos/preview            → genera el PDF a partir de un body completo (sin Venta)

Ambos devuelven el PDF (application/pdf) listo para descargar o mandar por WhatsApp.
"""
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

import jarvis_contratos as contratos

try:
    import models
except Exception:  # pragma: no cover
    models = None


# ─── SCHEMA del mini-formulario (lo que el aliado completa) ──────────────────
class ContratoBody(BaseModel):
    # Datos fiscales del cliente (lo que el sistema no sabe todavía)
    cliente_razon_social: Optional[str] = None   # si no viene, se toma de la Venta
    cliente_cuit: Optional[str] = ""
    cliente_domicilio: Optional[str] = ""
    cliente_representante: Optional[str] = ""
    cliente_cargo: Optional[str] = ""
    cliente_email: Optional[str] = ""
    cliente_pais: Optional[str] = "Argentina"      # define la terminología fiscal (CUIT/RUC/RFC/cédula…)
    cliente_condicion_fiscal: Optional[str] = ""   # situación tributaria del cliente
    cliente_dni: Optional[str] = ""                # documento del firmante (persona física)
    numero_contrato: Optional[str] = ""            # opcional; se autogenera si va vacío
    # Mantenimiento opcional (Anexo II) y mora (cláusula condicional)
    incluir_mantenimiento: Optional[bool] = False
    plan_mantenimiento: Optional[str] = "Plan Cuidado"
    mantenimiento_precio: Optional[float] = None
    incluir_mora: Optional[bool] = False
    interes_mora_mensual: Optional[float] = 3.0

    # Operación (opcionales; se autocompletan desde la Venta / PLAN_INFO)
    plan: Optional[str] = None
    precio_usd: Optional[float] = None
    moneda: Optional[str] = "USD"
    precio_ars: Optional[float] = None
    tipo_cambio: Optional[float] = None
    factura_tipo: Optional[str] = "B"
    iva_incluido: Optional[bool] = True
    forma_pago: Optional[str] = "pago único, sin costo mensual obligatorio"
    anticipo_pct: Optional[int] = 100
    link_pago: Optional[str] = ""

    # Lugar / fecha de firma
    ciudad: Optional[str] = "Santa Fe"
    fecha: Optional[str] = None        # ISO 'YYYY-MM-DD'; si no, hoy
    formato: Optional[str] = "pdf"     # "pdf" | "docx"

    class Config:
        extra = "allow"


def _body_to_extra(body: ContratoBody) -> dict:
    """Pasa solo los campos no nulos del body a un dict para datos_desde_venta."""
    return {k: v for k, v in body.dict().items() if v is not None}


def _file_response(datos: contratos.DatosContrato, formato: str = "pdf") -> StreamingResponse:
    import io
    formato = (formato or "pdf").lower()
    if formato not in ("pdf", "docx"):
        formato = "pdf"
    data = contratos.render_contrato(datos, formato)
    fname = contratos.nombre_archivo(datos, formato)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=contratos.MIME[formato],
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ─── REGISTER ────────────────────────────────────────────────────────────────
def register(app, get_db_func, auth_dep, ajustar_creditos_fn=None):
    """
    Inyecta las rutas de contratos en la app FastAPI.
    Firma idéntica a jarvis_routes.register para que lo registres igual.
    """

    @app.post("/ventas/{venta_id}/contrato")
    def generar_contrato_de_venta(
        venta_id: int,
        body: ContratoBody,
        request: Request,
        db: Session = Depends(get_db_func),
        aliado=Depends(auth_dep),
    ):
        if models is None:
            raise HTTPException(500, "models no disponible en el servidor")

        venta = db.query(models.Venta).filter(models.Venta.id == venta_id).first()
        if not venta:
            raise HTTPException(404, "Venta no encontrada")

        # Seguridad: la venta debe pertenecer al aliado autenticado
        if getattr(venta, "aliado_id", None) != getattr(aliado, "id", None):
            raise HTTPException(403, "Esta venta no te pertenece")

        datos = contratos.datos_desde_venta(venta, extra=_body_to_extra(body))
        try:
            return _file_response(datos, body.formato)
        except Exception as e:
            raise HTTPException(503, f"No se pudo generar el PDF: {e}")

    @app.post("/contratos/preview")
    def generar_contrato_preview(
        body: ContratoBody,
        request: Request,
        aliado=Depends(auth_dep),
    ):
        """Genera el contrato sin Venta (útil para ad-hoc o pruebas)."""
        extra = _body_to_extra(body)
        datos = contratos.DatosContrato(
            cliente_razon_social=extra.get("cliente_razon_social", ""),
            **{k: v for k, v in extra.items() if k != "cliente_razon_social"},
        )
        try:
            return _file_response(datos, body.formato)
        except Exception as e:
            raise HTTPException(503, f"No se pudo generar el PDF: {e}")