"""
rate_limit.py — instancia compartida del rate limiter (slowapi).

Vive en su propio módulo para que tanto main.py como los routers migrados
(p. ej. capturas.py, que limita /leads/capturar y /auditorias/log) puedan
decorar endpoints con @limiter.limit sin importar main (los decoradores se
evalúan al importar el módulo, así que un import diferido no sirve acá).

main.py sigue siendo el dueño del wiring: app.state.limiter = limiter,
el exception handler de RateLimitExceeded y el SlowAPIMiddleware.

Usa cliente in-memory por IP. Para multi-instancia mover a Redis.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])