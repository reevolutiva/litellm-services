"""Contenedor de tracking server-side (T0.6).

Recibe el envelope de eventos del data layer (§5.1 del SSOT del producto,
`reev-consultores/docs/00_design/02_fase1_discovery_consultor.md`) —del
navegador a través del REST de WordPress, o del propio WordPress vía
`wp_remote_post`— y los reenvía a la HTTP Tracking API de Matomo con
`token_auth`, para que el evento quede atribuido a la IP y a la fecha reales del
usuario y no a las del servidor.

Superficie HTTP:

    GET  /healthz             liveness (sin dependencias)
    GET  /readyz[?probe=1]    readiness (y opcionalmente contra Matomo)
    GET  /metrics             Prometheus (texto)
    GET  /                    identidad, catálogo y sitios
    POST /collect             el ingreso de eventos

Es **stateless**: no escribe nada en disco y no guarda estado de sesión. Lo
único con estado es la caché de deduplicación, en memoria y acotada, que se
puede perder sin consecuencias (ver `dedupe.py`).
"""

from __future__ import annotations

import json
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from . import settings as settings_module
from .dedupe import TtlDedupe
from .events import (
    CATALOGO,
    CONSENTIMIENTO_OTORGADO,
    Envelope,
    ValidationError,
    build_tracking_params,
    parse_envelope,
)
from .matomo import ForwardError, MatomoForwarder
from .metrics import (
    METRIC_BUILD_INFO,
    METRIC_BUSINESS_DUPLICATES,
    METRIC_DEDUPE_ENTRIES,
    METRIC_DISCARDED,
    METRIC_EVENTS,
    METRIC_FORWARD,
    METRIC_FORWARD_SECONDS,
    Metrics,
)
from .settings import ConfigError, Settings


def _log(**campos: Any) -> None:
    """Un evento por línea, en JSON: se lee con `kubectl logs | jq`."""
    campos.setdefault("ts", datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
    print(json.dumps(campos, ensure_ascii=False), file=sys.stdout, flush=True)


def _client_ip(request: Request, env: Envelope) -> str | None:
    """IP del usuario final.

    El camino normal (navegador → REST de WordPress → contenedor) trae la IP en
    `contexto.ip`, porque el salto intermedio la reemplaza por la suya. Si no
    viene, se usa `X-Forwarded-For` (Traefik) y por último el peer.
    """
    ip = env.contexto.get("ip")
    if isinstance(ip, str) and ip.strip():
        return ip.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        primera = xff.split(",")[0].strip()
        if primera:
            return primera
    return request.client.host if request.client else None


def _client_ua(request: Request, env: Envelope) -> str | None:
    ua = env.contexto.get("ua")
    if isinstance(ua, str) and ua.strip():
        return ua.strip()
    return request.headers.get("user-agent")


def create_app(settings: Settings, *,
               forwarder: MatomoForwarder | None = None,
               metrics: Metrics | None = None) -> FastAPI:
    dedupe = TtlDedupe(settings.dedupe_ttl_seconds, settings.dedupe_max_entries)
    metrica = metrics or Metrics()
    reenviador = forwarder or MatomoForwarder(
        settings.matomo_url,
        timeout=settings.forward_timeout_seconds,
        retries=settings.forward_retries,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for aviso in settings.warnings:
            _log(level="warning", msg=aviso)
        _log(
            level="info",
            msg="collector arriba",
            version=settings.version,
            matomo=settings.matomo_url,
            sitios=sorted(settings.sites),
            eventos=len(CATALOGO),
            token_matomo=bool(settings.matomo_token),
            dimensiones=settings.dimensiones,
            shadow_mode=settings.shadow_mode,
        )
        metrica.gauge(
            METRIC_BUILD_INFO, 1, {"version": settings.version},
            help_text="Versión del collector en ejecución",
        )
        yield
        await reenviador.aclose()

    app = FastAPI(
        title="reev-tracking-collector",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/docs",
    )

    @app.get("/", include_in_schema=False)
    async def raiz() -> dict[str, Any]:
        return {
            "service": "reev-tracking-collector",
            "version": settings.version,
            "contrato": "reev-consultores docs/00_design/02_fase1_discovery_consultor.md §5",
            "eventos": sorted(CATALOGO),
            "sitios": {
                slug: {"idsite": site.idsite, "dominio": site.dominio}
                for slug, site in sorted(settings.sites.items())
            },
            "dimensiones": settings.dimensiones,
            "shadow_mode": settings.shadow_mode,
        }

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(probe: int = 0) -> JSONResponse:
        if not settings.sites:
            return JSONResponse({"status": "not_ready", "detalle": "sin sitios"}, 503)
        if probe:
            sano, detalle = await reenviador.probe(settings.matomo_token)
            if not sano:
                return JSONResponse(
                    {"status": "not_ready", "matomo": detalle}, 503
                )
            return JSONResponse({"status": "ready", "matomo": detalle}, 200)
        return JSONResponse({"status": "ready"}, 200)

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint() -> PlainTextResponse:
        metrica.gauge(
            METRIC_DEDUPE_ENTRIES, dedupe.size,
            help_text="Entradas en la caché de deduplicación",
        )
        return PlainTextResponse(metrica.render(), media_type="text/plain; version=0.0.4")

    @app.post("/collect")
    async def collect(request: Request) -> JSONResponse:
        inicio = time.perf_counter()
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > settings.request_max_bytes:
            return _rechazo(metrica, "invalid", 413, "payload demasiado grande", site=None)

        try:
            body = json.loads(await request.body() or b"{}")
        except json.JSONDecodeError:
            return _rechazo(metrica, "invalid", 400, "cuerpo JSON inválido", site=None)
        if not isinstance(body, dict):
            return _rechazo(metrica, "invalid", 400, "el cuerpo debe ser un objeto", site=None)

        # --- Envelope §5.1: allow-list, descarte de lo no declarado y de PII ---
        try:
            env = parse_envelope(body)
        except ValidationError as exc:
            return _rechazo(metrica, "invalid", 400, str(exc), site=None)

        for descarte in env.descartes:
            metrica.inc(METRIC_DISCARDED, {"motivo": descarte.motivo}, help_text=(
                "Campos recibidos que no se reenviaron (allow-list del envelope §5.1)"
            ))
            _log(level="info", evento=env.evento, campo=descarte.campo, motivo=descarte.motivo,
                 site=env.consultor_slug, outcome="discarded")

        site = settings.site(env.consultor_slug)
        if site is None:
            return _rechazo(metrica, "unknown_site", 404,
                            f"sitio desconocido: {env.consultor_slug}", site=env.consultor_slug)

        token = _bearer(request)
        if not site.autorizado(token):
            return _rechazo(metrica, "unauthorized", 401, "token de sitio inválido o ausente",
                            site=env.consultor_slug)

        # El `idsite` es del contenedor (aislamiento por sitio, G6/§7): el del
        # payload se compara para detectar un sitio mal configurado, y nunca
        # manda. Un desacuerdo se registra en vez de rechazar el evento.
        idsite_declarado = env.idsite_declarado
        if idsite_declarado is not None and idsite_declarado != site.idsite:
            _log(level="warning", evento=env.evento, site=env.consultor_slug,
                 detalle="idsite del payload distinto del configurado",
                 idsite_payload=idsite_declarado, idsite_config=site.idsite)

        # --- Consentimiento (Ley 21.719) -------------------------------------
        # Sin `otorgado` no se envía nada al destino. El evento se cuenta para
        # que la negativa sea visible y no un agujero en el informe.
        if env.consentimiento != CONSENTIMIENTO_OTORGADO:
            _log(level="info", evento=env.evento, site=env.consultor_slug,
                 consentimiento=env.consentimiento, outcome="consent_denied")
            metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                        "outcome": "consent_denied"},
                        help_text="Eventos recibidos por resultado")
            return JSONResponse({"status": "consent_denied", "forwarded": False}, 200)

        # --- Deduplicación (§5.3.2): por `evento_id` y por clave de negocio ---
        # Primero el `evento_id` (el mismo evento reenviado: reintento, doble vía
        # navegador/servidor) y después la clave de negocio, que es la garantía
        # fuerte: una compra = una fila aunque WooCommerce dispare tres hooks con
        # tres identificadores distintos. Los dos casos se cuentan por separado
        # para que el del hook repetido sea visible en las métricas.
        clave_evento = f"ev|{env.consultor_slug}|{env.evento_id}"
        clave_negocio = env.clave_negocio()
        clave_negocio_completa = (
            f"bn|{env.consultor_slug}|{env.evento}|{clave_negocio}" if clave_negocio else None
        )

        if not dedupe.reserve(clave_evento):
            metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                        "outcome": "duplicate"},
                        help_text="Eventos recibidos por resultado")
            _log(level="info", evento=env.evento, site=env.consultor_slug, evento_id=env.evento_id,
                 emisor=env.emisor, outcome="duplicate")
            return JSONResponse({"status": "duplicate", "motivo": "evento_id",
                                 "evento_id": env.evento_id, "idsite": site.idsite,
                                 "forwarded": False}, 200)

        if clave_negocio_completa and not dedupe.reserve(clave_negocio_completa):
            metrica.inc(METRIC_BUSINESS_DUPLICATES,
                        {"evento": env.evento, "site": env.consultor_slug}, help_text=(
                            "Eventos descartados por clave de negocio (mismo hecho, otro evento_id)"
                        ))
            metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                        "outcome": "duplicate_business"},
                        help_text="Eventos recibidos por resultado")
            _log(level="info", evento=env.evento, site=env.consultor_slug, evento_id=env.evento_id,
                 clave_negocio=clave_negocio, outcome="duplicate_business")
            return JSONResponse({"status": "duplicate", "motivo": "clave_de_negocio",
                                 "evento_id": env.evento_id, "idsite": site.idsite,
                                 "forwarded": False}, 200)

        reservadas = [clave_evento] + ([clave_negocio_completa] if clave_negocio_completa else [])
        try:
            params = build_tracking_params(
                env=env,
                idsite=site.idsite,
                dominio=site.dominio,
                token_auth=settings.matomo_token,
                client_ip=_client_ip(request, env),
                client_ua=_client_ua(request, env),
                dimensiones=settings.dimensiones,
                ahora=datetime.now(timezone.utc).isoformat(),
            )
        except ValidationError as exc:
            for clave in reservadas:
                dedupe.release(clave)
            return _rechazo(metrica, "invalid", 400, str(exc), site=env.consultor_slug)

        if settings.shadow_mode:
            for clave in reservadas:
                dedupe.commit(clave)
            metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                        "outcome": "shadow"}, help_text="Eventos recibidos por resultado")
            _log(level="info", evento=env.evento, site=env.consultor_slug, evento_id=env.evento_id,
                 emisor=env.emisor, outcome="shadow")
            return JSONResponse({"status": "accepted", "shadow": True, "params": params}, 202)

        try:
            status = await reenviador.forward(params)
        except ForwardError as exc:
            # Ni pérdida ni duplicado: se libera la reserva y el emisor reintenta.
            for clave in reservadas:
                dedupe.release(clave)
            metrica.inc(METRIC_FORWARD, {"result": "error"}, help_text="Intentos de reenvío a Matomo")
            metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                        "outcome": "forward_failed"},
                        help_text="Eventos recibidos por resultado")
            _log(level="error", evento=env.evento, site=env.consultor_slug, evento_id=env.evento_id,
                 outcome="forward_failed", matomo_status=exc.status, detalle=exc.detail)
            return JSONResponse(
                {"status": "forward_failed", "evento_id": env.evento_id, "retryable": True,
                 "matomo_status": exc.status}, 502
            )

        for clave in reservadas:
            dedupe.commit(clave)
        metrica.inc(METRIC_FORWARD, {"result": "ok"}, help_text="Intentos de reenvío a Matomo")
        metrica.inc(METRIC_EVENTS, {"evento": env.evento, "site": env.consultor_slug,
                                    "outcome": "accepted"},
                    help_text="Eventos recibidos por resultado")
        metrica.observe(METRIC_FORWARD_SECONDS, time.perf_counter() - inicio,
                        help_text="Segundos de reenvío a Matomo (incluye reintentos)")
        _log(level="info", evento=env.evento, site=env.consultor_slug, idsite=site.idsite,
             evento_id=env.evento_id, emisor=env.emisor, clave_negocio=clave_negocio,
             outcome="accepted", matomo_status=status, descartados=len(env.descartes),
             latencia_ms=round((time.perf_counter() - inicio) * 1000, 1))
        return JSONResponse({"status": "accepted", "evento_id": env.evento_id,
                             "idsite": site.idsite}, 202)

    return app


def _bearer(request: Request) -> str | None:
    cabecera = request.headers.get("authorization") or ""
    if cabecera.lower().startswith("bearer "):
        return cabecera[7:].strip() or None
    return request.headers.get("x-reev-token")


def _rechazo(metrica: Metrics, outcome: str, status: int, detalle: str,
             site: str | None) -> JSONResponse:
    metrica.inc(METRIC_EVENTS, {"evento": "-", "site": site or "-", "outcome": outcome},
                help_text="Eventos recibidos por resultado")
    _log(level="warning", site=site, outcome=outcome, detalle=detalle, status=status)
    return JSONResponse({"status": outcome, "detalle": detalle, "retryable": False}, status)


def app_factory() -> FastAPI:
    """Entrypoint para `uvicorn --factory`: config inválida = el pod no arranca."""
    try:
        settings = settings_module.load_settings()
    except ConfigError as exc:
        _log(level="critical", msg=f"config inválida: {exc}")
        raise
    return create_app(settings)
