"""
routes — sub-paquete con los APIRouter por dominio.

Estructura sugerida (a poblar gradualmente desde main.py):

    routes/
        academia.py       Modulos de academia, completar modulo                 [migrado, EJEMPLO]
        aliados.py        Login, registro, perfil, red de sub-aliados           TODO
        bolsa.py          Marketplace, comprar, reclamar, reportar              TODO
        creditos.py       Paquetes, solicitudes, transacciones                  TODO
        comisiones.py     Continuidad, pagos, abonar                            TODO
        comunidad.py      Posts, comentarios                                    TODO
        admin.py          Todo lo admin (excepto el de cada dominio)            TODO
        webhooks.py       MP, PayPal, callbacks                                 TODO
        jobs.py           Scheduler + cron jobs                                 TODO

En main.py basta con:

    from routes import academia, aliados, bolsa, ...
    app.include_router(academia.router)
    app.include_router(aliados.router)
    ...

Recomendacion: migra UN router por commit, corre los endpoints contra
admin.html / portal.html para no romper nada, y solo despues borrar las
versiones viejas de main.py. NO mezcles dos commits.
"""