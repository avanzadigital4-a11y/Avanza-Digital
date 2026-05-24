"""
jarvis_knowledge.py — Knowledge Graph Industrial de JARVIS

Base de conocimiento estructurada para los 3 sectores iniciales.
No es un documento de texto — es un grafo de entidades relacionadas que JARVIS
puede navegar para responder con contexto sectorial específico.

SECTORES CUBIERTOS (Fase 1):
  - Metalúrgica / Metalmecánica
  - Agro / Agroindustria
  - Logística / Transporte

FUNCIONES PÚBLICAS:
  get_sector_knowledge(sector)  → Devuelve el bloque completo de un sector
  get_sector_prompt(sector)     → Bloque listo para inyectar en system prompt
  get_objections(sector)        → Objeciones + respuestas de un sector
  get_buyer_profile(sector, cargo) → Perfil del comprador típico de un sector/cargo
  get_battle_card(sector)       → Cómo vender Avanza en ese sector
  detectar_sector(texto)        → Infiere el sector a partir de texto libre (NLP liviano)
  listar_sectores()             → Lista todos los sectores disponibles

DISEÑO:
  Datos estáticos curados por el equipo de Avanza + enriquecidos por IA.
  Se puede extender sin cambiar la interfaz — agregar más sectores en SECTORS.
  No requiere base de datos — es conocimiento de campo codificado como Python dicts.
  Se carga en memoria al importar el módulo (≈2ms).
"""

from __future__ import annotations
from typing import Optional

# ═══════════════════════════════════════════════════════════════════════════════
# KNOWLEDGE GRAPH — ESTRUCTURA POR SECTOR
# ═══════════════════════════════════════════════════════════════════════════════

SECTORS: dict[str, dict] = {

    # ─────────────────────────────────────────────────────────────────────────
    # METALÚRGICA / METALMECÁNICA
    # ─────────────────────────────────────────────────────────────────────────
    "metalurgica": {
        "nombre_display": "Metalúrgica / Metalmecánica",
        "aliases": ["metalurgica", "metalmecanica", "metalmecánica", "acero", "mecanizado",
                    "torneria", "soldadura", "fundicion", "matriceria", "herreria",
                    "corte laser", "plasma", "aluminio", "inoxidable", "chapa"],

        "descripcion": (
            "Empresas que fabrican, procesan o transforman metales. "
            "Incluye tornería, matricería, soldadura, corte láser, mecanizado CNC, "
            "fundición, herrería artística e industrial y fabricación de estructuras. "
            "En Argentina, concentradas en: Gran Buenos Aires, Gran Rosario, Córdoba, "
            "San Nicolás, Rafaela, Mendoza y Gran La Plata."
        ),

        "tamanio_tipico": "De 5 a 200 empleados. El sweet spot de Avanza: 15-80 empleados.",

        "ciclo_compra": {
            "duracion_promedio_dias": 45,
            "descripcion": (
                "Ciclo de decisión moderado. El dueño o gerente de producción decide, "
                "a veces necesita una segunda reunión. Las grandes (>100 empleados) "
                "pasan por comité de dirección — el ciclo sube a 60-90 días."
            ),
            "estacionalidad": {
                "pico": "Marzo-Junio y Agosto-Octubre — cuando el agro y la construcción compran",
                "baja": "Enero (feria) y Julio (mitad de año, recesión de flujo de caja)",
            }
        },

        "decisores": {
            "dueño_gerente_general": {
                "perfil": "Técnico de formación, desconfía del marketing, decide por resultados verificables",
                "miedo_real": "Gastar en algo que no trae clientes — ya fue quemado antes",
                "motivacion_real": "Llenar el taller cuando el mercado afloja sin sumar personal de ventas",
                "argumento_que_funciona": "Mostrarle leads reales de empresas similares. Un caso concreto vale más que 10 slides",
                "que_no_decir": ["posicionamiento de marca", "awareness", "presencia en redes sociales"],
            },
            "gerente_de_produccion": {
                "perfil": "Ingeniero o técnico, prioriza eficiencia y no meterse en problemas",
                "miedo_real": "Quedar mal con dirección si una inversión nueva no funciona",
                "motivacion_real": "Mejorar las consultas que llegan sin aumentar su carga de trabajo",
                "argumento_que_funciona": "Que el sistema funciona solo — no necesita que él lo opere",
                "que_no_decir": ["es muy fácil de usar", "en 2 horas lo aprendés"],
            },
            "responsable_comercial": {
                "perfil": "Generalmente el dueño o un familiar — no suele haber un área comercial dedicada",
                "miedo_real": "Que el proveedor no entienda el negocio industrial y haga algo genérico",
                "motivacion_real": "Más consultas de clientes serios, no tirar tiempo en llamadas sin valor",
                "argumento_que_funciona": "Especialización industrial — no somos una agencia de marketing genérica",
                "que_no_decir": ["te vamos a armar una estrategia de contenidos", "engagement"],
            }
        },

        "dolores_tipicos": [
            "La web no genera consultas — tienen una página vieja que no atrae clientes",
            "No aparecen en Google cuando alguien busca su servicio en la zona",
            "Todo viene por boca a boca — no saben cómo crecer sin referencias",
            "Clientes de gran empresa no los consideran si no tienen presencia digital seria",
            "Perdieron licitaciones porque el comprador no encontró información online de la empresa",
            "El dueño es el único que vende — si él no está, no entra nada",
            "Les cuesta diferenciarse de talleres más chicos que cobran menos",
        ],

        "objeciones_frecuentes": {
            "ya_tenemos_pagina": {
                "objecion": "Ya tenemos página web",
                "respuesta": (
                    "Justamente — tener web y tener una web que genera consultas son dos cosas "
                    "distintas. ¿Les llegan más de 3 consultas nuevas por semana desde la web ahora mismo? "
                    "Si no, la web está ahí pero no está trabajando. "
                    "Eso es exactamente lo que resolvemos."
                ),
                "tip": "No atacar la web que tienen — ofrecer medirla antes de criticarla"
            },
            "no_es_el_momento": {
                "objecion": "No es el momento, estamos con mucho trabajo",
                "respuesta": (
                    "Perfecto — cuando hay trabajo es el mejor momento para armar el sistema que mantiene "
                    "ese ritmo cuando el mercado cambia. La mayoría de nuestros clientes arrancó "
                    "cuando estaban ocupados, precisamente para no depender de que el teléfono suene solo. "
                    "¿15 minutos esta semana o la próxima para mostrarte los números?"
                ),
                "tip": "Este es el momento más frecuente de apertura — aprovechar que están bien"
            },
            "mandeme_info": {
                "objecion": "Mándeme información por mail",
                "respuesta": (
                    "Le mando algo mejor: un análisis gratuito de la presencia digital actual de [empresa] "
                    "versus sus competidores directos en [zona]. En 24 horas le muestro dónde están parados. "
                    "¿A qué mail se lo envío?"
                ),
                "tip": "Convertir el pedido de info en un diagnóstico — cambia la dinámica"
            },
            "cuanto_sale": {
                "objecion": "¿Cuánto sale?",
                "respuesta": (
                    "Antes de hablar de precio necesito entender qué le generaría a ustedes. "
                    "Un sistema que trae 5 clientes nuevos por mes vale mucho más que uno que no trae ninguno, "
                    "aunque cuesten igual. ¿Me contás brevemente de qué tamaño son los proyectos que buscan atraer?"
                ),
                "tip": "Nunca dar el precio sin contexto de valor — siempre primero ROI"
            },
            "ya_tenemos_proveedor": {
                "objecion": "Ya trabajamos con alguien / ya tenemos agencia",
                "respuesta": (
                    "¿Y les está trayendo consultas concretas del sector? "
                    "La mayoría de las agencias generales no conoce el ciclo de compra industrial ni cómo "
                    "posicionar una empresa metalúrgica frente a compradores técnicos. "
                    "¿Cuántas consultas nuevas llegaron por ese canal el mes pasado?"
                ),
                "tip": "No atacar al proveedor — hacer una pregunta de diagnóstico que el cliente no puede responder bien"
            },
        },

        "argumentos_efectivos": [
            "Especialización: solo trabajamos con empresas industriales — entendemos el ciclo de compra B2B",
            "Resultado measurable: el objetivo son consultas calificadas, no 'seguidores' ni 'alcance'",
            "Casos similares: mostrar empresas del mismo sector que aumentaron consultas",
            "ROI calculado: con 2 clientes nuevos por año ya se paga el plan anual",
            "Sin equipo de ventas: el sistema trabaja mientras el dueño hace lo suyo",
        ],

        "roi_benchmark": {
            "ticket_promedio_cliente_del_cliente": "desde $300.000 ARS hasta $2.000.000+ por proyecto",
            "clientes_nuevos_para_roi": "Con 1-2 proyectos nuevos por año, el plan ya se paga",
            "tiempo_primeros_resultados": "45-90 días para primeras consultas orgánicas",
        },

        "competidores_comunes": {
            "agencia_local_generica": {
                "fortaleza": "precio bajo, relación personal",
                "debilidad": "no conoce el sector, entrega webs genéricas sin SEO industrial",
                "como_ganar": "especialización + casos del sector + diagnóstico gratis"
            },
            "freelance": {
                "fortaleza": "muy barato, flexible",
                "debilidad": "sin proceso, sin garantía, sin soporte continuo, riesgo de desaparición",
                "como_ganar": "continuidad + proceso + respaldo de Avanza"
            },
        },

        "vocabulario_tecnico": [
            "mecanizado CNC", "tolerancias", "tratamiento superficial", "templado",
            "galvanizado", "anodizado", "matricería", "estampado", "corte láser",
            "plasma", "electroerosión", "soldadura TIG/MIG", "fundición", "inyección",
            "ASTM", "SAE", "normas IRAM", "ISO 9001", "proveedor homologado",
            "licitación", "OC (orden de compra)", "remito", "planos técnicos"
        ],

        "canales_prospeccion": {
            "mejor": "WhatsApp directo al gerente/dueño + LinkedIn para empresas medianas",
            "funciona": "Email frío si el asunto menciona el sector específico",
            "evitar": "Instagram, Facebook — los decisores industriales no buscan proveedores ahí"
        },

        "momento_optimo_contacto": "Martes o miércoles, 9-11am. Evitar viernes tarde y lunes a primera hora.",
    },


    # ─────────────────────────────────────────────────────────────────────────
    # AGRO / AGROINDUSTRIA
    # ─────────────────────────────────────────────────────────────────────────
    "agro": {
        "nombre_display": "Agro / Agroindustria",
        "aliases": ["agro", "agroindustria", "campo", "agricultura", "ganaderia",
                    "soja", "maiz", "trigo", "semilla", "insumos agricolas", "maquinaria agricola",
                    "cosecha", "silo", "acopio", "frigorifico", "tambo", "feed lot",
                    "cooperativa", "agroexportadora", "semillero", "agroquimica"],

        "descripcion": (
            "Empresas del agro y la agroindustria: productores, acopios, cooperativas, "
            "proveedores de insumos, maquinaria agrícola, frigoríficos, tambos y empresas "
            "de servicios al campo. En Argentina: zona núcleo (Santa Fe, Córdoba, Entre Ríos, "
            "Buenos Aires norte), NOA (Tucumán, Salta) y Patagonia (frutas de pepita y carozo)."
        ),

        "tamanio_tipico": "Muy variable: desde el productor individual hasta la cooperativa de 500+ empleados.",

        "ciclo_compra": {
            "duracion_promedio_dias": 38,
            "descripcion": (
                "El productor/dueño decide rápido cuando ve valor concreto. "
                "Las cooperativas y empresas medianas tienen comité y el ciclo puede llegar a 60 días. "
                "Los proveedores de insumos compran cuando el mercado agro está bien — seguir la macro."
            ),
            "estacionalidad": {
                "pico": "Febrero-Abril (post cosecha gruesa, hay plata) y Agosto-Octubre (pre campaña)",
                "baja": "Julio-Agosto (entre campañas, flujo de caja apretado) y Diciembre-Enero",
            }
        },

        "decisores": {
            "dueno_productor": {
                "perfil": "Práctico, desconfiado de lo que no puede medir. 'Si no veo resultados en 90 días, corto'",
                "miedo_real": "Gastar en marketing y que no llegue ni un comprador real",
                "motivacion_real": "Diversificar canales de venta, no depender de un solo acopio o broker",
                "argumento_que_funciona": "Mostrarle cómo otros productores de la zona venden directo online",
                "que_no_decir": ["estrategia de marca", "posicionamiento", "comunidad digital"],
            },
            "gerente_comercial_cooperativa": {
                "perfil": "Profesional, orienta decisiones con números. Tiene que justificar ante el consejo",
                "miedo_real": "Presentar un proyecto que no funcione y quedar mal ante los socios",
                "motivacion_real": "Ampliar la base de clientes, atraer productores de otras zonas",
                "argumento_que_funciona": "ROI calculado + casos de cooperativas comparables + propuesta piloto de 3 meses",
                "que_no_decir": ["es fácil de implementar", "no te va a llevar tiempo"],
            },
            "dueno_agroquimica_insumos": {
                "perfil": "Empresario con mentalidad comercial clara. Ya sabe de inversión en ventas",
                "miedo_real": "Que el sistema no llegue al agricultor en el momento de decisión de compra",
                "motivacion_real": "Capturar consultas de productores en campaña, antes de que vayan al competidor",
                "argumento_que_funciona": "SEO local + landing de campaña = estar primero cuando el productor busca",
                "que_no_decir": ["redes sociales", "contenido orgánico"],
            }
        },

        "dolores_tipicos": [
            "Venden todo por relaciones personales — si el vendedor se va, se va la cartera",
            "Los productores de otras zonas no los conocen — están limitados a su radio de 80km",
            "Los competidores grandes aparecen primero en Google en su propia zona",
            "No capturan consultas fuera del horario de atención (el campo trabaja en horarios raros)",
            "Las cooperativas buscan modernizarse pero sin saber por dónde empezar",
            "Los exportadores y compradores grandes piden presencia web antes de habilitar como proveedor",
        ],

        "objeciones_frecuentes": {
            "el_campo_no_usa_internet": {
                "objecion": "El campo no busca proveedores por Internet",
                "respuesta": (
                    "Eso era así hace 5 años. Hoy el 73% de los productores chequea online "
                    "antes de llamar a un proveedor — especialmente los de segunda generación que "
                    "manejan el campo con el celular. La pregunta no es si buscan online, "
                    "sino si te encuentran cuando lo hacen."
                ),
                "tip": "Tener un dato concreto del sector — cambiar la percepción primero"
            },
            "no_es_el_momento_campania": {
                "objecion": "Estamos en campaña, no tenemos tiempo",
                "respuesta": (
                    "Exacto — por eso lo mejor es armarlo ahora. La campaña termina en 3 meses "
                    "y cuando el productor busca insumos para la próxima, ya estarías posicionado. "
                    "El sistema lo armamos nosotros, no vos."
                ),
                "tip": "Usar la estacionalidad a favor — sembrar ahora, cosechar después"
            },
            "ya_trabajo_con_acopio": {
                "objecion": "Todo lo vendo a través del acopio / cooperativa",
                "respuesta": (
                    "¿Y eso te hace depender de que ellos te prioricen sobre sus otros proveedores? "
                    "El canal digital te da un acceso directo al productor — no competís con el acopio, "
                    "le sumás otro canal que el acopio no puede darte."
                ),
                "tip": "No reemplazar el canal actual — complementarlo"
            },
        },

        "argumentos_efectivos": [
            "Canal directo: llegar al productor sin intermediario",
            "Disponibilidad 24/7: el campo trabaja en horarios fuera de oficina",
            "Posicionamiento geográfico: aparecer en búsquedas de 'proveedor X en zona Y'",
            "Exportadores y grandes compradores verifican presencia web antes de habilitar proveedores",
            "La generación joven del campo decide con el celular en la mano",
        ],

        "roi_benchmark": {
            "ticket_promedio_cliente_del_cliente": "desde $500.000 ARS (insumos) hasta millones por campaña",
            "clientes_nuevos_para_roi": "Con 1 productor nuevo por cuatrimestre, el plan se paga ampliamente",
            "tiempo_primeros_resultados": "30-60 días para primeras consultas con SEO local activo",
        },

        "competidores_comunes": {
            "agencia_generica": {
                "fortaleza": "precio",
                "debilidad": "no entiende el ciclo agrícola ni el vocabulario del campo",
                "como_ganar": "especialización + conocimiento del ciclo de campaña + vocabulario correcto"
            },
            "directorio_agro": {
                "fortaleza": "visibilidad en el sector",
                "debilidad": "pasivo — el cliente no genera leads activos",
                "como_ganar": "presencia activa + SEO + generación de consultas calificadas"
            },
        },

        "vocabulario_tecnico": [
            "lote", "campaña", "cosecha gruesa", "cosecha fina", "insumos", "fitosanitarios",
            "semilla fiscalizada", "siembra directa", "acopio", "silo bag", "tonelada",
            "exportación", "FOB", "cooperativa", "consignatario", "corredor de granos",
            "feed lot", "hacienda", "cabezas", "cuero", "trozado", "media res",
            "tambo", "litros/vaca/día", "remitente", "RENSPA", "SENASA"
        ],

        "canales_prospeccion": {
            "mejor": "WhatsApp (los productores viven en WhatsApp) + contacto en exposiciones rurales",
            "funciona": "Email frío con asunto que menciona la zona específica + referidos",
            "evitar": "LinkedIn para productores chicos — funciona para cooperativas y empresas medianas"
        },

        "momento_optimo_contacto": "Lunes o martes, 8-10am. Evitar durante cosecha gruesa (Marzo-Abril: están en el campo).",
    },


    # ─────────────────────────────────────────────────────────────────────────
    # LOGÍSTICA / TRANSPORTE
    # ─────────────────────────────────────────────────────────────────────────
    "logistica": {
        "nombre_display": "Logística / Transporte",
        "aliases": ["logistica", "transporte", "flota", "camion", "flete", "distribucion",
                    "deposito", "almacen", "warehouse", "carga", "courier", "paqueteria",
                    "frigorifico logistico", "cadena de frio", "ultima milla", "intermodal",
                    "agente de carga", "operador logistico", "3pl", "4pl"],

        "descripcion": (
            "Empresas de transporte de cargas, operadores logísticos, distribuidores, "
            "depósitos fiscales, agentes de carga y empresas de última milla. "
            "En Argentina: Gran Buenos Aires (Nodo Nacional), Rosario (eje exportación), "
            "Córdoba (centro del país), Mendoza (distribución cuyo-patagonia)."
        ),

        "tamanio_tipico": "Desde el transportista con 3-5 camiones hasta operadores con 200+ unidades.",

        "ciclo_compra": {
            "duracion_promedio_dias": 35,
            "descripcion": (
                "El dueño de la empresa transportista decide rápido cuando entiende el ROI. "
                "Los operadores logísticos medianos tienen proceso de aprobación más largo. "
                "La decisión se acelera cuando están buscando carga activamente."
            ),
            "estacionalidad": {
                "pico": "Septiembre-Noviembre (pico pre-navidad, agro de segunda campaña) y Marzo-Mayo",
                "baja": "Enero-Febrero (feria y arranque de año lento), Julio",
            }
        },

        "decisores": {
            "dueno_empresa_transporte": {
                "perfil": "Práctico, orientado a números: costo por kilómetro, ocupación de la flota",
                "miedo_real": "Camiones sin carga — la pesadilla del transportista",
                "motivacion_real": "Llenar la flota con clientes propios, no depender de buscadores de carga o intermediarios",
                "argumento_que_funciona": "Cálculo concreto: cuánto vale un camión parado vs. cuánto cuesta el plan",
                "que_no_decir": ["presencia en redes", "branding", "estrategia de contenidos"],
            },
            "gerente_operaciones": {
                "perfil": "Enfocado en eficiencia, no en marketing. Habla de TMS, GPS, tiempos de entrega",
                "miedo_real": "Que el sistema genere consultas que su operación no puede atender",
                "motivacion_real": "Crecer con un flujo de clientes predecible que él pueda planificar",
                "argumento_que_funciona": "El sistema atrae el tipo de carga que mejor encaja con su flota",
                "que_no_decir": ["muchas consultas", "vamos a explotar de trabajo"],
            },
            "responsable_comercial": {
                "perfil": "Si existe, es un vendedor de campo que trabaja con relaciones personales",
                "miedo_real": "Que lo remplacen o que el canal digital compita con su trabajo",
                "motivacion_real": "Tener leads calificados para cerrar, no prospectar en frío",
                "argumento_que_funciona": "El digital genera los leads, él los cierra — equipo",
                "que_no_decir": ["esto puede reemplazar el trabajo de venta presencial"],
            }
        },

        "dolores_tipicos": [
            "Dependen de intermediarios (buscadores de carga, despachantes) que se quedan con el margen",
            "Flota ociosa en temporada baja — camiones parados son pérdida directa",
            "Los clientes grandes exigen presencia digital antes de homologar a un proveedor",
            "No aparecen cuando un cliente busca 'flete X' o 'transporte refrigerado en Y'",
            "No diferencian su servicio del transportista más barato de la zona",
            "Todo viene por conocidos — cuando el conocido se va, se va la carga",
        ],

        "objeciones_frecuentes": {
            "el_transporte_es_boca_a_boca": {
                "objecion": "El transporte siempre fue boca a boca",
                "respuesta": (
                    "Exacto, y eso funciona mientras los conocidos sigan teniendo carga. "
                    "¿Qué pasa cuando una empresa con la que trabajás 5 años terceriza su logística "
                    "o cierra? El canal digital es el que te da clientes que no te conocen todavía. "
                    "¿Cuántos clientes nuevos (que no te conocían antes) entraron el año pasado?"
                ),
                "tip": "La pregunta final siempre desnuda la dependencia del boca a boca"
            },
            "tengo_siempre_camiones_llenos": {
                "objecion": "Ahora no me falta carga",
                "respuesta": (
                    "Perfecto — cuando la flota está completa es el mejor momento para construir "
                    "el canal propio. Así, cuando un cliente grande se va o el mercado cambia, "
                    "ya tenés otro flujo funcionando. Las empresas de transporte que más crecieron "
                    "armaron el digital cuando estaban bien, no cuando les faltaba carga."
                ),
                "tip": "Mismo argumento que metalúrgica — sembrar en la abundancia"
            },
            "es_caro": {
                "objecion": "Es mucho dinero para una empresa de transporte",
                "respuesta": (
                    "Hagamos el cálculo inverso: ¿cuánto le cuesta a la empresa tener un camión "
                    "parado una semana? Si el sistema trae 1 cliente que ocupa un camión 4 semanas "
                    "al mes, el plan se paga en los primeros 15 días de ese contrato. "
                    "¿Cuánto vale el flete mensual de un cliente regular?"
                ),
                "tip": "Siempre llevar al cálculo del camión parado — es el ROI más fácil de visualizar"
            },
        },

        "argumentos_efectivos": [
            "Canal propio: no depender de intermediarios que se quedan con el margen",
            "Homologación: los clientes grandes piden presencia web para aprobar proveedores",
            "Especialización por tipo de carga: aparecer cuando buscan 'transporte refrigerado' o 'carga pesada en X'",
            "ROI directo: costo del plan vs. costo de un camión parado — el número es contundente",
            "Captura fuera de horario: los gerentes de logística del cliente buscan proveedores de noche o el fin de semana",
        ],

        "roi_benchmark": {
            "ticket_promedio_cliente_del_cliente": "desde $150.000 ARS/mes (cliente chico) hasta $800.000+ (contrato de distribución)",
            "clientes_nuevos_para_roi": "Con 1 cliente nuevo que ocupa 1 camión regularmente, el plan se paga en el primer mes",
            "tiempo_primeros_resultados": "30-60 días para aparición en búsquedas locales",
        },

        "competidores_comunes": {
            "buscadores_de_carga": {
                "fortaleza": "tráfico inmediato de carga",
                "debilidad": "comisión alta, carga eventual no recurrente, el cliente no es tuyo",
                "como_ganar": "canal propio = cliente fidelizable + sin comisión"
            },
            "agencia_generica": {
                "fortaleza": "precio",
                "debilidad": "no conoce el vocabulario logístico ni cómo buscan los gerentes de supply chain",
                "como_ganar": "especialización + SEO con términos técnicos del sector"
            },
        },

        "vocabulario_tecnico": [
            "flete", "OTM (operador de transporte multimodal)", "carga fraccionada",
            "grupaje", "carga completa (FTL)", "carga parcial (LTL)", "cabotaje",
            "internacional", "ADR (mercancías peligrosas)", "temperatura controlada",
            "cadena de frío", "última milla", "cross-docking", "fulfillment",
            "TMS", "WMS", "track and trace", "Bill of Lading", "remito de carga",
            "guía de transporte", "seguro de carga", "siniestro", "tiempo de tránsito"
        ],

        "canales_prospeccion": {
            "mejor": "Email frío con asunto específico de servicio + LinkedIn para operadores medianos/grandes",
            "funciona": "WhatsApp para transportistas más chicos",
            "evitar": "Instagram — no es el canal donde buscan proveedores de logística"
        },

        "momento_optimo_contacto": "Martes o jueves, 9-11am. Evitar Lunes (cierre operativo del fin de semana) y viernes tarde.",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Aliases globales para lookup flexible
# ─────────────────────────────────────────────────────────────────────────────

_ALIAS_MAP: dict[str, str] = {}
for _sector_key, _sector_data in SECTORS.items():
    for _alias in _sector_data.get("aliases", []):
        _ALIAS_MAP[_alias.lower()] = _sector_key
    _ALIAS_MAP[_sector_key] = _sector_key


# ═══════════════════════════════════════════════════════════════════════════════
# FUNCIONES PÚBLICAS
# ═══════════════════════════════════════════════════════════════════════════════

def listar_sectores() -> list[str]:
    """Retorna la lista de sectores disponibles en el Knowledge Graph."""
    return list(SECTORS.keys())


def _resolver_sector(sector: str) -> Optional[str]:
    """Resuelve un nombre o alias de sector a la clave canónica. Retorna None si no existe."""
    if not sector:
        return None
    clave = _ALIAS_MAP.get(sector.lower().strip())
    return clave


def get_sector_knowledge(sector: str) -> Optional[dict]:
    """
    Retorna el bloque completo de conocimiento de un sector.
    Acepta el nombre canónico o cualquier alias definido en el grafo.

    >>> get_sector_knowledge("metalurgica")
    >>> get_sector_knowledge("mecanizado")  # alias → metalurgica
    >>> get_sector_knowledge("agro")
    """
    clave = _resolver_sector(sector)
    if not clave:
        return None
    return SECTORS.get(clave)


def get_sector_prompt(sector: str) -> str:
    """
    Retorna un bloque de texto listo para inyectar en el system prompt de JARVIS.
    Si el sector no existe, retorna un bloque genérico mínimo.
    """
    data = get_sector_knowledge(sector)
    if not data:
        return (
            f"SECTOR: {sector} — no hay Knowledge Graph específico para este sector.\n"
            "Respondé de forma general sobre marketing digital B2B industrial.\n"
        )

    obj_text = ""
    for key, obj in data.get("objeciones_frecuentes", {}).items():
        obj_text += (
            f"  - Objeción '{obj['objecion']}':\n"
            f"    Respuesta: {obj['respuesta'][:180]}...\n"
        )

    decisores_text = ""
    for cargo, perfil in data.get("decisores", {}).items():
        decisores_text += (
            f"  - {cargo.replace('_', ' ').title()}: "
            f"miedo real = '{perfil.get('miedo_real', '')}'; "
            f"argumento que funciona = '{perfil.get('argumento_que_funciona', '')}'\n"
        )

    dolores = "\n  - ".join(data.get("dolores_tipicos", []))
    argumentos = "\n  - ".join(data.get("argumentos_efectivos", []))
    vocabulario = ", ".join(data.get("vocabulario_tecnico", [])[:12])

    roi = data.get("roi_benchmark", {})

    return f"""
CONOCIMIENTO SECTORIAL — {data['nombre_display'].upper()}
══════════════════════════════════════════════════════════════

DESCRIPCIÓN: {data['descripcion']}

CICLO DE COMPRA:
  Duración promedio: {data['ciclo_compra']['duracion_promedio_dias']} días
  {data['ciclo_compra']['descripcion']}
  Pico de actividad: {data['ciclo_compra']['estacionalidad'].get('pico', 'no especificado')}
  Temporada baja: {data['ciclo_compra']['estacionalidad'].get('baja', 'no especificada')}

PERFILES DE DECISORES:
{decisores_text}

DOLORES TÍPICOS DEL SECTOR:
  - {dolores}

ARGUMENTOS QUE FUNCIONAN EN ESTE SECTOR:
  - {argumentos}

OBJECIONES FRECUENTES Y CÓMO MANEJARLAS:
{obj_text}

ROI BENCHMARKS:
  Ticket promedio del cliente: {roi.get('ticket_promedio_cliente_del_cliente', 'n/d')}
  Clientes nuevos para ROI: {roi.get('clientes_nuevos_para_roi', 'n/d')}
  Tiempo a primeros resultados: {roi.get('tiempo_primeros_resultados', 'n/d')}

VOCABULARIO TÉCNICO DEL SECTOR (usarlo en emails y propuestas):
  {vocabulario}

CANAL Y MOMENTO ÓPTIMO DE CONTACTO:
  {data.get('canales_prospeccion', {}).get('mejor', 'WhatsApp + Email')}
  Momento: {data.get('momento_optimo_contacto', 'Martes-Miércoles 9-11am')}
""".strip()


def get_objections(sector: str) -> Optional[dict]:
    """
    Retorna el diccionario de objeciones + respuestas de un sector.

    >>> obj = get_objections("metalurgica")
    >>> obj["ya_tenemos_pagina"]["respuesta"]
    """
    data = get_sector_knowledge(sector)
    if not data:
        return None
    return data.get("objeciones_frecuentes", {})


def get_buyer_profile(sector: str, cargo: str) -> Optional[dict]:
    """
    Retorna el perfil de comprador para un sector y cargo específico.
    El cargo se busca de forma flexible (no necesita ser exacto).

    >>> get_buyer_profile("metalurgica", "gerente de producción")
    >>> get_buyer_profile("agro", "dueño")
    """
    data = get_sector_knowledge(sector)
    if not data:
        return None

    decisores = data.get("decisores", {})
    cargo_lower = cargo.lower().strip()

    # Búsqueda exacta primero
    if cargo_lower in decisores:
        return decisores[cargo_lower]

    # Búsqueda flexible
    for key, perfil in decisores.items():
        if any(word in key for word in cargo_lower.split()):
            return perfil

    # Si no encontró, devolver el perfil del dueño/decisor principal
    for key in ["dueno_gerente_general", "dueno_productor", "dueno_empresa_transporte"]:
        if key in decisores:
            return decisores[key]

    return list(decisores.values())[0] if decisores else None


def get_battle_card(sector: str) -> Optional[dict]:
    """
    Retorna una battle card para vender Avanza en ese sector.
    Incluye: argumentos clave, objeciones frecuentes con respuestas, competidores y ROI.
    """
    data = get_sector_knowledge(sector)
    if not data:
        return None

    return {
        "sector": data["nombre_display"],
        "argumentos_clave": data.get("argumentos_efectivos", []),
        "dolores_tipicos": data.get("dolores_tipicos", []),
        "objeciones_top": {
            k: {
                "objecion": v["objecion"],
                "respuesta": v["respuesta"],
            }
            for k, v in list(data.get("objeciones_frecuentes", {}).items())[:3]
        },
        "roi": data.get("roi_benchmark", {}),
        "ciclo_dias": data.get("ciclo_compra", {}).get("duracion_promedio_dias", 45),
        "canal_optimo": data.get("canales_prospeccion", {}).get("mejor", ""),
        "momento_optimo": data.get("momento_optimo_contacto", ""),
        "vocabulario_clave": data.get("vocabulario_tecnico", [])[:8],
        "competidores": data.get("competidores_comunes", {}),
    }


def detectar_sector(texto: str) -> Optional[str]:
    """
    Detecta el sector más probable a partir de texto libre.
    NLP liviano: cuenta coincidencias de aliases por sector.

    Retorna la clave canónica del sector detectado, o None si no hay señal suficiente.

    >>> detectar_sector("empresa de mecanizado cnc en córdoba")
    'metalurgica'
    >>> detectar_sector("transporte de soja en la pampa")
    'agro'
    """
    if not texto:
        return None

    texto_lower = texto.lower()
    scores: dict[str, int] = {}

    for alias, sector_key in _ALIAS_MAP.items():
        if alias in texto_lower:
            scores[sector_key] = scores.get(sector_key, 0) + 1

    if not scores:
        return None

    mejor_sector = max(scores, key=scores.get)
    if scores[mejor_sector] == 0:
        return None

    return mejor_sector


def get_roi_pitch(sector: str, *, plan_precio_ars: float = 2900.0) -> str:
    """
    Genera un pitch de ROI específico para el sector y el precio del plan.
    Listo para usar en propuestas o respuestas a 'cuánto sale'.

    >>> print(get_roi_pitch("metalurgica", plan_precio_ars=2900))
    """
    data = get_sector_knowledge(sector)
    if not data:
        return (
            f"Con un plan de ${plan_precio_ars:,.0f}/mes, necesitás 1 cliente nuevo "
            f"para que el sistema se pague. El resto es ganancia neta."
        )

    roi = data.get("roi_benchmark", {})
    ticket = roi.get("ticket_promedio_cliente_del_cliente", "significativo")
    tiempo = roi.get("tiempo_primeros_resultados", "30-90 días")
    clientes = roi.get("clientes_nuevos_para_roi", "1-2 clientes nuevos")

    return (
        f"En el sector {data['nombre_display']}, el ticket promedio es {ticket}. "
        f"Con {clientes} por año, el plan de ${plan_precio_ars:,.0f}/mes ya se paga — "
        f"el resto es rentabilidad neta. "
        f"Los primeros resultados suelen aparecer en {tiempo}."
    )