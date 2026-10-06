"""Tests del contenedor de tracking server-side (T0.6).

Se corren sin red y sin Matomo: el reenvío se sustituye con un transporte de
`httpx` que captura los parámetros, así que lo que se prueba es exactamente lo
que el contenedor le manda a Matomo, no una aproximación.

Cada test cita la cláusula del contrato que verifica:
`reev-consultores/docs/00_design/02_fase1_discovery_consultor.md` §5.

    ./run-tests.sh          # dentro de la imagen
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.dedupe import TtlDedupe
from app.events import CATALOGO
from app.main import create_app
from app.matomo import MatomoForwarder
from app.settings import Settings, Site

TOKEN_MATOMO = "token-matomo-de-prueba"
TOKEN_SITIO = "token-sitio-de-prueba"

UUID_COMPRA = "b0a1c2d3-4e5f-4a6b-8c9d-0e1f2a3b4c5d"


class MatomoFalso:
    """Doble de Matomo: captura y devuelve los status que se le pidan."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.requests: list[dict[str, str]] = []
        self.statuses = list(statuses or [])

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(dict(httpx.QueryParams(request.content.decode())))
            status = self.statuses.pop(0) if self.statuses else 204
            return httpx.Response(status, text="" if status < 400 else "error de matomo")
        return httpx.MockTransport(handler)

    @property
    def eventos(self) -> list[dict[str, str]]:
        return list(self.requests)


@pytest.fixture()
def config() -> Settings:
    return Settings(
        matomo_url="http://matomo.local",
        matomo_token=TOKEN_MATOMO,
        sites={
            "urano": Site("urano", 3, "urano.consulting", TOKEN_SITIO),
            "sin-token": Site("sin-token", 4, "sin-token.cl", None),
            "sin-dominio": Site("sin-dominio", 5, "", TOKEN_SITIO),
        },
        require_site_token=False,
        forward_retries=1,
    )


@pytest.fixture()
def matomo() -> MatomoFalso:
    return MatomoFalso()


def _app(config: Settings, matomo: MatomoFalso):
    forwarder = MatomoForwarder(
        config.matomo_url, retries=1, client=httpx.AsyncClient(transport=matomo.transport())
    )
    return create_app(config, forwarder=forwarder)


@pytest.fixture()
def cliente(config: Settings, matomo: MatomoFalso) -> Any:
    app = _app(config, matomo)
    with TestClient(app) as c:
        yield c


def sobre(**overrides: Any) -> dict[str, Any]:
    """Envelope §5.1 de una compra confirmada (el caso del corte)."""
    base: dict[str, Any] = {
        "evento": "compra_confirmada",
        "evento_version": 1,
        "evento_id": UUID_COMPRA,
        "ocurrido_en": "2026-09-25T21:00:00Z",
        "emisor": "servidor",
        "sitio": {"consultor_slug": "urano", "dominio": "urano.consulting", "idsite_matomo": 3},
        "tramo": {"origen": "t3", "destino": "t4"},
        "producto": {"slug": "estrategia-2027", "tipo": "bootcamp"},
        "cohorte": {"slug": "2026-11-estrategia-2027"},
        "usuario_wp_id": 1234,
        "visita_id": "1a2b3c4d5e6f7a8b",
        "consentimiento": "otorgado",
        "origen": {"utm_source": "instagram", "utm_medium": "social",
                   "utm_campaign": "e2027-lanzamiento"},
        "datos": {"orden_id": 9012, "monto": 490000, "moneda": "CLP"},
        "destino": {"matomo": {"e_c": "compra", "e_a": "compra_confirmada"}},
        "pagina": {"url": "https://urano.consulting/finalizar-compra/order-received/9012/"},
        "contexto": {"ip": "190.160.1.2", "ua": "Mozilla/5.0 (prueba)"},
    }
    base.update(overrides)
    return base


def auth(token: str = TOKEN_SITIO) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- Criterio de aceptación del corte (§8.2) --------------------------------


def test_compra_confirmada_llega_a_matomo(cliente: TestClient, matomo: MatomoFalso) -> None:
    """§5.4: una compra entra como evento de ecommerce con `uid`, `cdt` y visita."""
    respuesta = cliente.post("/collect", json=sobre(), headers=auth())
    assert respuesta.status_code == 202, respuesta.text
    assert respuesta.json() == {"status": "accepted", "evento_id": UUID_COMPRA, "idsite": 3}
    assert len(matomo.eventos) == 1

    params = matomo.eventos[0]
    assert params["idsite"] == "3"
    assert params["rec"] == "1"
    assert params["ca"] == "1"                 # no es page view
    assert params["send_image"] == "0"         # 204 en vez de GIF
    assert params["e_c"] == "compra"
    assert params["e_a"] == "compra_confirmada"
    assert params["e_n"] == "estrategia-2027:2026-11-estrategia-2027"
    assert params["e_v"] == "490000"           # numérico, sin decimales
    assert params["uid"] == "wp:1234"
    assert params["_id"] == "1a2b3c4d5e6f7a8b"
    assert params["cdt"] == "2026-09-25 21:00:00"
    assert params["cip"] == "190.160.1.2"
    assert params["url"] == "https://urano.consulting/finalizar-compra/order-received/9012/"
    assert params["mtm_campaign"] == "e2027-lanzamiento"
    assert params["mtm_source"] == "instagram"
    assert params["token_auth"] == TOKEN_MATOMO
    # Dimensiones del data layer map: `evento_id` es la auditoría de duplicados.
    assert params["dimension1"] == "estrategia-2027"
    assert params["dimension2"] == "2026-11-estrategia-2027"
    assert params["dimension3"] == "t4"
    assert params["dimension4"] == "urano"
    assert params["dimension5"] == UUID_COMPRA


def test_los_tres_eventos_del_corte_se_traducen(cliente: TestClient, matomo: MatomoFalso) -> None:
    registro = sobre(
        evento="registro_completado",
        evento_id="c0ffee00-0000-4000-8000-000000000001",
        datos={"rol": "subscriber"},
        producto={},
        cohorte={},
        tramo={"origen": "t2", "destino": "t3"},
        pagina={"url": "https://urano.consulting/mi-cuenta/"},
    )
    inicio = sobre(
        evento="curso_iniciado",
        evento_id="c0ffee00-0000-4000-8000-000000000002",
        datos={"course_id": 55, "course_name": "Estrategia 2027", "origen_deteccion": "navegador"},
        producto={},
        cohorte={},
        pagina={"url": "https://urano.consulting/cursos/estrategia-2027/"},
    )
    assert cliente.post("/collect", json=registro, headers=auth()).status_code == 202
    assert cliente.post("/collect", json=inicio, headers=auth()).status_code == 202

    primero, segundo = matomo.eventos
    assert (primero["e_c"], primero["e_a"], primero["e_n"]) == (
        "escalera", "registro_completado", "subscriber"
    )
    assert (segundo["e_c"], segundo["e_a"], segundo["e_n"]) == (
        "curso", "curso_iniciado", "Estrategia 2027"
    )
    assert segundo["uid"] == "wp:1234"
    # La clave de negocio del progreso es (usuario, curso), §5.3.2.
    assert segundo["dimension5"] == "c0ffee00-0000-4000-8000-000000000002"


def test_url_se_deriva_del_dominio_si_no_hay_pagina(
    config: Settings, matomo: MatomoFalso
) -> None:
    """Matomo exige `url`; el envelope §5.1 no la trae. Sin `pagina.url` se usa
    el dominio del inquilino, y sin dominio se rechaza en vez de inventarla."""
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        sin_pagina = sobre(pagina={})
        assert cliente.post("/collect", json=sin_pagina, headers=auth()).status_code == 202
        assert matomo.eventos[0]["url"] == "https://urano.consulting/"

        sin_dominio = sobre(
            evento_id="c0ffee00-0000-4000-8000-000000000003",
            sitio={"consultor_slug": "sin-dominio"},
            pagina={},
        )
        rechazo = cliente.post("/collect", json=sin_dominio, headers=auth())
        assert rechazo.status_code == 400
        assert "Matomo exige una URL" in rechazo.json()["detalle"]


# --- Criterio de aceptación: no se duplican ni se pierden (§5.3.2) -----------


def test_evento_repetido_no_se_reenvia(cliente: TestClient, matomo: MatomoFalso) -> None:
    """Idempotencia por `evento_id`: el mismo evento dos veces = una fila."""
    primera = cliente.post("/collect", json=sobre(), headers=auth())
    segunda = cliente.post("/collect", json=sobre(), headers=auth())
    tercera = cliente.post("/collect", json=sobre(), headers=auth())

    assert primera.status_code == 202
    assert segunda.status_code == 200 and segunda.json()["status"] == "duplicate"
    assert segunda.json()["motivo"] == "evento_id"
    assert tercera.json()["status"] == "duplicate"
    assert len(matomo.eventos) == 1


def test_woocommerce_tres_hooks_una_compra(cliente: TestClient, matomo: MatomoFalso) -> None:
    """Prueba en negativo del caso WooCommerce (§5.2, §5.3.2).

    `woocommerce_payment_complete`, `woocommerce_order_status_completed` y
    `woocommerce_order_status_processing` disparan por la misma orden con
    `evento_id` **distintos**: sin la clave de negocio, la compra se cuenta tres
    veces. Con ella, entra una sola vez.
    """
    for sufijo in ("a1", "b2", "c3"):
        cuerpo = sobre(evento_id=f"11111111-2222-4333-8444-0000000000{sufijo}")
        respuesta = cliente.post("/collect", json=cuerpo, headers=auth())
        assert respuesta.status_code in (200, 202), respuesta.text

    assert len(matomo.eventos) == 1, "una compra tiene que ser una fila en Matomo"

    metricas = cliente.get("/metrics").text
    assert 'reev_tracking_business_duplicates_total{evento="compra_confirmada",site="urano"} 2' in metricas


def test_clave_de_negocio_no_mezcla_ordenes_distintas(
    cliente: TestClient, matomo: MatomoFalso
) -> None:
    """El negativo del negativo: dos compras distintas siguen siendo dos filas."""
    cliente.post("/collect", json=sobre(), headers=auth())
    otro = sobre(
        evento_id="22222222-3333-4444-8555-666666666666",
        datos={"orden_id": 9013, "monto": 19000, "moneda": "CLP"},
    )
    cliente.post("/collect", json=otro, headers=auth())
    assert len(matomo.eventos) == 2


def _curso(usuario: int, evento_id: str, course_id: int = 55) -> dict[str, Any]:
    return sobre(
        evento="curso_iniciado",
        evento_id=evento_id,
        usuario_wp_id=usuario,
        datos={"course_id": course_id, "course_name": "Estrategia 2027"},
        producto={},
        cohorte={},
        tramo={"destino": "t4"},
        pagina={"url": "https://urano.consulting/cursos/estrategia-2027/"},
    )


def test_el_alumno_entra_en_la_clave_de_negocio(cliente: TestClient, matomo: MatomoFalso) -> None:
    """Prueba en negativo del falso duplicado (§5.3.2 + `usa_alumno`).

    El objeto del hecho (curso, lección, grupo, tramo) lo comparten todos los
    alumnos. Si la clave de negocio fuese solo el objeto, el segundo alumno que
    abre el mismo curso se descartaría como `duplicate_business`: un evento
    perdido, que es exactamente lo que el §5.3.2 no puede permitir.
    """
    assert cliente.post("/collect", json=_curso(1234, "aaaa1111-0000-4000-8000-00000000aaa1"),
                        headers=auth()).status_code == 202
    segundo = cliente.post("/collect", json=_curso(5678, "aaaa1111-0000-4000-8000-00000000aaa2"),
                           headers=auth())

    assert segundo.status_code == 202, segundo.text
    assert len(matomo.eventos) == 2, "dos alumnos en el mismo curso son dos eventos"
    assert [p["uid"] for p in matomo.eventos] == ["wp:1234", "wp:5678"]
    assert [p["e_a"] for p in matomo.eventos] == ["curso_iniciado", "curso_iniciado"]


def test_curso_iniciado_se_reconcilia_entre_navegador_y_servidor(
    cliente: TestClient, matomo: MatomoFalso
) -> None:
    """El mismo hecho por los dos caminos (§5.3.1): un solo evento.

    El navegador emite `curso_iniciado` con su `evento_id`; si el JS no corrió
    (bloqueador, acceso directo), el servidor lo re-emite al primer
    `leccion_completada` con **otro** `evento_id` y la misma clave de negocio
    (alumno + curso). Tiene que quedar una sola fila.
    """
    del_navegador = _curso(1234, "bbbb2222-0000-4000-8000-00000000bbb1")
    del_navegador["emisor"] = "navegador"
    assert cliente.post("/collect", json=del_navegador, headers=auth()).status_code == 202

    del_servidor = _curso(1234, "bbbb2222-0000-4000-8000-00000000bbb2")
    del_servidor["datos"]["origen_deteccion"] = "servidor"
    reconciliacion = cliente.post("/collect", json=del_servidor, headers=auth())

    assert reconciliacion.status_code == 200
    assert reconciliacion.json()["motivo"] == "clave_de_negocio"
    assert len(matomo.eventos) == 1, "el mismo hecho por dos caminos es una fila"


def test_sin_alumno_no_hay_clave_de_negocio(cliente: TestClient, matomo: MatomoFalso) -> None:
    """Sin alumno la clave queda incompleta: no se arma, en vez de deduplicar contra nadie."""
    anonimo = _curso(0, "cccc3333-0000-4000-8000-00000000ccc1")
    anonimo["usuario_wp_id"] = None
    assert cliente.post("/collect", json=anonimo, headers=auth()).status_code == 202
    segundo = _curso(0, "cccc3333-0000-4000-8000-00000000ccc2")
    segundo["usuario_wp_id"] = None
    assert cliente.post("/collect", json=segundo, headers=auth()).status_code == 202
    assert len(matomo.eventos) == 2


def test_fallo_de_matomo_no_pierde_el_evento(config: Settings) -> None:
    """Un 502 libera las reservas: el reintento del emisor vuelve a entrar."""
    matomo = MatomoFalso(statuses=[500, 500, 204])
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        fallo = cliente.post("/collect", json=sobre(), headers=auth())
        assert fallo.status_code == 502
        assert fallo.json()["retryable"] is True
        assert fallo.json()["evento_id"] == UUID_COMPRA  # §8.5: el id en el error

        reintento = cliente.post("/collect", json=sobre(), headers=auth())
        assert reintento.status_code == 202, reintento.text
    # Dos intentos del primer POST (1 + 1 reintento) y uno del reintento.
    assert len(matomo.eventos) == 3


# --- §5.3.3 / §8.4: allow-list y descarte de PII ----------------------------


def test_campo_no_declarado_se_descarta_y_se_cuenta(
    cliente: TestClient, matomo: MatomoFalso
) -> None:
    cuerpo = sobre(
        **{
            "campo_inventado": "no declarado en el envelope",
            "datos": {"orden_id": 9012, "monto": 490000, "cupon": "NO-DECLARADO"},
            "producto": {"slug": "estrategia-2027", "precio_interno": 12},
        }
    )
    assert cliente.post("/collect", json=cuerpo, headers=auth()).status_code == 202

    params = matomo.eventos[0]
    assert "cupon" not in params.values()
    assert params["e_v"] == "490000"
    metricas = cliente.get("/metrics").text
    # Tres descartes: uno en la raíz, uno en `datos`, uno en `producto`.
    assert 'reev_tracking_discarded_fields_total{motivo="campo_no_declarado"} 3' in metricas


def test_pii_se_descarta_aunque_el_campo_exista(cliente: TestClient, matomo: MatomoFalso) -> None:
    """Un email en un campo declarado no viaja a Matomo ni al `uid`."""
    cuerpo = sobre(
        **{
            "usuario_wp_id": 1234,
            "datos": {"orden_id": 9012, "monto": 490000, "email": "alguien@ejemplo.cl"},
            "origen": {"utm_source": "instagram", "utm_campaign": "contacto@urano.consulting"},
        }
    )
    assert cliente.post("/collect", json=cuerpo, headers=auth()).status_code == 202

    params = matomo.eventos[0]
    assert "alguien@ejemplo.cl" not in " ".join(params.values())
    assert "contacto@urano.consulting" not in " ".join(params.values())
    assert params["uid"] == "wp:1234"

    metricas = cliente.get("/metrics").text
    assert 'reev_tracking_discarded_fields_total{motivo="pii"} 2' in metricas


def test_usuario_wp_id_nunca_es_email(cliente: TestClient, matomo: MatomoFalso) -> None:
    """§5.1: `usuario_wp_id` es entero. Un email ahí es un error de contrato."""
    respuesta = cliente.post(
        "/collect", json=sobre(usuario_wp_id="alguien@ejemplo.cl"), headers=auth()
    )
    assert respuesta.status_code == 400
    assert "usuario_wp_id" in respuesta.json()["detalle"]
    assert matomo.eventos == []


# --- Consentimiento (Ley 21.719) --------------------------------------------


@pytest.mark.parametrize("estado", ["rechazado", "pendiente"])
def test_sin_consentimiento_otorgado_no_se_reenvia(
    cliente: TestClient, matomo: MatomoFalso, estado: str
) -> None:
    respuesta = cliente.post("/collect", json=sobre(consentimiento=estado), headers=auth())
    assert respuesta.status_code == 200
    assert respuesta.json() == {"status": "consent_denied", "forwarded": False}
    assert matomo.eventos == []


def test_sin_consentimiento_no_hay_uid(cliente: TestClient, matomo: MatomoFalso) -> None:
    """§5.4: `uid` solo con consentimiento. El resto del evento sí se mide."""
    cuerpo = sobre(consentimiento="otorgado", usuario_wp_id=None)
    assert cliente.post("/collect", json=cuerpo, headers=auth()).status_code == 202
    assert "uid" not in matomo.eventos[0]


# --- Contrato y errores ------------------------------------------------------


@pytest.mark.parametrize(
    ("cuerpo", "status", "detalle"),
    [
        (sobre(evento="purchase"), 400, "evento desconocido"),
        (sobre(evento_version=2), 400, "evento_version"),
        (sobre(evento_id=""), 400, "falta `evento_id`"),
        (sobre(ocurrido_en=""), 400, "falta `ocurrido_en`"),
        (sobre(ocurrido_en="ayer"), 400, "ISO 8601"),
        (sobre(emisor="telepata"), 400, "emisor"),
        (sobre(consentimiento="quizas"), 400, "consentimiento"),
        (sobre(sitio={}), 400, "sitio.consultor_slug"),
        (sobre(datos={"monto": 490000}), 400, "datos incompletos"),
        (sobre(evento="curso_iniciado", datos={}), 400, "datos incompletos"),
        (sobre(sitio={"consultor_slug": "otro-consultor"}), 404, "sitio desconocido"),
    ],
)
def test_rechazos(cliente: TestClient, matomo: MatomoFalso, cuerpo: dict[str, Any],
                  status: int, detalle: str) -> None:
    respuesta = cliente.post("/collect", json=cuerpo, headers=auth())
    assert respuesta.status_code == status, respuesta.text
    assert detalle in respuesta.json()["detalle"]
    assert matomo.eventos == []


def test_token_invalido_da_401(config: Settings, matomo: MatomoFalso) -> None:
    config.require_site_token = True
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        assert cliente.post("/collect", json=sobre()).status_code == 401
        assert cliente.post("/collect", json=sobre(), headers=auth("otro")).status_code == 401
        assert cliente.post("/collect", json=sobre(), headers=auth()).status_code == 202
    assert len(matomo.eventos) == 1


def test_idsite_del_payload_no_manda(cliente: TestClient, matomo: MatomoFalso) -> None:
    """El `idsite` es del contenedor: el del payload no puede cambiar el destino."""
    cuerpo = sobre(sitio={"consultor_slug": "urano", "idsite_matomo": 999})
    assert cliente.post("/collect", json=cuerpo, headers=auth()).status_code == 202
    assert matomo.eventos[0]["idsite"] == "3"


def test_sin_token_de_matomo_se_omite_token_auth(config: Settings, matomo: MatomoFalso) -> None:
    config.matomo_token = None
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        assert cliente.post("/collect", json=sobre(), headers=auth()).status_code == 202
    assert "token_auth" not in matomo.eventos[0]


def test_dimensiones_desactivadas(config: Settings, matomo: MatomoFalso) -> None:
    """Sin dimensiones configuradas el evento sigue siendo válido."""
    config.dimensiones = {}
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        assert cliente.post("/collect", json=sobre(), headers=auth()).status_code == 202
    assert not [clave for clave in matomo.eventos[0] if clave.startswith("dimension")]


def test_shadow_mode_no_reenvia(config: Settings, matomo: MatomoFalso) -> None:
    config.shadow_mode = True
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        respuesta = cliente.post("/collect", json=sobre(), headers=auth())
    assert respuesta.status_code == 202
    assert respuesta.json()["shadow"] is True
    assert matomo.eventos == []


def test_readyz_probe_con_token_exige_acceso(config: Settings) -> None:
    """Un 401 con `token_auth` en mano es una credencial rechazada, no "vida"."""
    matomo = MatomoFalso(statuses=[401])
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        respuesta = cliente.get("/readyz?probe=1")
    assert respuesta.status_code == 503
    assert "token_auth" in respuesta.json()["matomo"]


def test_readyz_probe_sin_token_no_juzga_credencial(config: Settings) -> None:
    """Sin token configurado, cualquier respuesta por debajo de 500 es vida."""
    config.matomo_token = None
    matomo = MatomoFalso(statuses=[401])
    app = _app(config, matomo)
    with TestClient(app) as cliente:
        respuesta = cliente.get("/readyz?probe=1")
    assert respuesta.status_code == 200
    assert "sin token_auth" in respuesta.json()["matomo"]


def test_health_ready_metrics_y_catalogo(cliente: TestClient) -> None:
    assert cliente.get("/healthz").json() == {"status": "ok"}
    assert cliente.get("/readyz").json() == {"status": "ready"}

    raiz = cliente.get("/").json()
    assert set(raiz["eventos"]) == set(CATALOGO)
    assert raiz["sitios"]["urano"] == {"idsite": 3, "dominio": "urano.consulting"}
    assert raiz["dimensiones"]["evento_id"] == 5

    cliente.post("/collect", json=sobre(), headers=auth())
    cliente.post("/collect", json=sobre(), headers=auth())

    cuerpo = cliente.get("/metrics").text
    assert "# TYPE reev_tracking_events_total counter" in cuerpo
    assert 'reev_tracking_events_total{evento="compra_confirmada",outcome="accepted",site="urano"} 1' in cuerpo
    assert 'reev_tracking_events_total{evento="compra_confirmada",outcome="duplicate",site="urano"} 1' in cuerpo
    assert "reev_tracking_forward_attempts_total" in cuerpo
    assert "reev_tracking_dedupe_entries" in cuerpo


# --- Unidades ----------------------------------------------------------------


def test_ttl_dedupe_reserva_commit_release() -> None:
    cache = TtlDedupe(ttl_seconds=60, max_entries=10)
    assert cache.reserve("a") is True
    assert cache.reserve("a") is False
    cache.release("a")
    assert cache.reserve("a") is True
    cache.commit("a")
    assert cache.reserve("a") is False

    for i in range(50):
        cache.reserve(f"k{i}")
    assert cache.size <= 10


def test_catalogo_cubierto_por_dominios_declarados() -> None:
    """Un `e_c` fuera de la lista cerrada del §5.4 sería una categoría inventada."""
    from app.events import CATEGORIAS

    assert CATALOGO, "el catálogo no puede estar vacío"
    for nombre, spec in CATALOGO.items():
        assert spec.categoria in CATEGORIAS, f"{nombre}: categoría {spec.categoria} fuera del §5.4"
        assert nombre == nombre.lower()
