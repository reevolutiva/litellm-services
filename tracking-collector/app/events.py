"""Traducción del evento canónico -> parámetros de la HTTP Tracking API de Matomo.

Contrato (congelado, no se inventa acá): §5 del SSOT del producto,
`reev-consultores/docs/00_design/02_fase1_discovery_consultor.md` (rama
`t1.1/escalera-valor-data-layer`, commit `3214af6`):

  - §5.1 envelope   → `ENVELOPE_*` / `_validar_envelope`
  - §5.2 catálogo   → `CATALOGO`
  - §5.3 reglas     → dedupe por `evento_id` y por clave de negocio (en `main.py`),
                      allow-list de campos y descarte de PII (acá)
  - §5.4 traducción → `build_tracking_params`

El productor (navegador, WordPress, CRM, operación) **no conoce Matomo**: manda
el catálogo y el contenedor traduce. Por eso `destino.matomo` del envelope se
acepta como pista y se **ignora**: el que decide `e_c`/`e_a`/`e_n` es este
módulo, a partir del catálogo. Si el destino cambia (PostHog, G6), se cambia
este archivo y el data layer del sitio queda intacto.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

MATOMO_TRACKER_PATH = "/matomo.php"

# Versión de envelope que este contenedor entiende (evento_version del §5.1).
# Sube solo si cambia el **significado** del payload: una versión mayor es un
# contrato que este contenedor no puede interpretar, así que se rechaza con 400
# en vez de traducir a ciegas.
EVENTO_VERSION_MAX = 1

# Dominios de `e_c` — lista cerrada del §5.4. Un dominio fuera de acá sería una
# categoría arbitraria en el informe del sitio: se rechaza.
CATEGORIAS = ("navegacion", "escalera", "curso", "compra", "comunidad", "crm", "privacidad")

# Emisores válidos del §5.1.
EMISORES = ("navegador", "servidor", "crm", "operacion")

# Consentimiento de analítica (Ley 21.719). Solo `otorgado` habilita el envío.
CONSENTIMIENTOS = ("otorgado", "rechazado", "pendiente")
CONSENTIMIENTO_OTORGADO = "otorgado"

# Matomo exige que `_id` sea un visitor id válido: 16 caracteres hex.
_HEX = set("0123456789abcdef")

# Un valor con forma de dirección de correo es PII aunque venga en un campo
# declarado (§5.3.3): se descarta el campo, no el evento, y se cuenta aparte.
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

# Claves que nunca entran al envelope, ni dentro de `datos`. La defensa real es
# la allow-list (un campo no declarado se descarta igual); esto existe para que
# la PII evidente quede marcada con su propio motivo en las métricas.
CLAVES_PII = frozenset(
    {
        "email", "correo", "correo_electronico", "mail",
        "telefono", "fono", "celular", "whatsapp",
        "rut", "dni", "documento", "pasaporte",
        "direccion", "comuna", "ciudad",
        "password", "clave", "tarjeta", "card", "cvv", "iban", "banco",
        "nombre_completo", "apellido", "apellidos", "first_name", "last_name",
    }
)


class ValidationError(ValueError):
    """Payload rechazado: 400. Nunca llega a Matomo."""


# ---------------------------------------------------------------------------
# Envelope (§5.1): allow-list, no deny-list
# ---------------------------------------------------------------------------

ENVELOPE_OK = frozenset(
    {
        "evento",
        "evento_version",
        "evento_id",
        "ocurrido_en",
        "emisor",
        "sitio",
        "tramo",
        "producto",
        "cohorte",
        "grupo",
        "usuario_wp_id",
        "visita_id",
        "consentimiento",
        "origen",
        "datos",
        "destino",
        "pagina",     # extensión T0.6 (ver README): Matomo exige `url`
        "contexto",   # extensión T0.6: el salto WordPress aporta IP y UA reales
    }
)

# Sub-objetos: cada uno con su allow-list. Un campo de más se descarta.
SUB_OK: dict[str, frozenset[str]] = {
    "sitio": frozenset({"consultor_slug", "dominio", "idsite_matomo"}),
    "tramo": frozenset({"origen", "destino"}),
    "producto": frozenset({"slug", "tipo", "nombre"}),
    "cohorte": frozenset({"slug", "nombre"}),
    "grupo": frozenset({"slug", "bp_group_id", "nombre"}),
    "origen": frozenset(
        {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "referrer"}
    ),
    "destino": frozenset({"matomo"}),
    "pagina": frozenset({"url", "titulo", "referrer"}),
    "contexto": frozenset({"ip", "ua"}),
}


# ---------------------------------------------------------------------------
# Catálogo (§5.2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EventSpec:
    """Cómo se representa un evento canónico dentro de Matomo (§5.4)."""

    categoria: str                       # → `e_c`
    requeridos: tuple[str, ...] = ()     # claves de `datos` obligatorias
    permitidos: tuple[str, ...] = ()     # allow-list de `datos`
    clave_negocio: tuple[str, ...] = ()  # §5.3.2: campos que identifican el hecho
    nombre_datos: str | None = None      # `datos.<clave>` como `e_n`
    valor_datos: str | None = None       # `datos.<clave>` como `e_v`
    usa_alumno: bool = False             # `usuario_wp_id` en la clave de negocio


# Eventos del corte (§8.2) + el resto del catálogo del §5.2. `e_a` es siempre el
# nombre canónico del evento y `e_c` su dominio (§5.4).
CATALOGO: dict[str, EventSpec] = {
    # --- corte ---------------------------------------------------------------
    "registro_completado": EventSpec(
        categoria="escalera",
        permitidos=("rol", "via", "sitio_origen"),
        clave_negocio=("usuario_wp_id",),
        nombre_datos="rol",
        usa_alumno=True,
    ),
    "compra_confirmada": EventSpec(
        categoria="compra",
        requeridos=("orden_id",),
        permitidos=("orden_id", "monto", "moneda", "estado", "items", "medio_pago"),
        clave_negocio=("orden_id",),
        valor_datos="monto",
    ),
    "curso_iniciado": EventSpec(
        categoria="curso",
        requeridos=("course_id",),
        permitidos=("course_id", "course_name", "leccion_id", "origen_deteccion"),
        clave_negocio=("course_id",),
        nombre_datos="course_name",
        usa_alumno=True,
    ),
    # --- escalera y adquisición ---------------------------------------------
    "visita_iniciada": EventSpec(
        categoria="navegacion",
        permitidos=("landing", "referrer_externo", "nueva_sesion"),
    ),
    "consentimiento_otorgado": EventSpec(
        categoria="privacidad", permitidos=("base_legal", "cmp_version")
    ),
    "consentimiento_revocado": EventSpec(
        categoria="privacidad", permitidos=("base_legal", "cmp_version")
    ),
    "lead_capturado": EventSpec(
        categoria="crm",
        permitidos=("formulario", "producto_interes", "origen_crm"),
        clave_negocio=("contacto_id",),
    ),
    "suscriptor_confirmado": EventSpec(
        categoria="crm", permitidos=("lista", "estado"), clave_negocio=("contacto_id",)
    ),
    "etiqueta_aplicada": EventSpec(
        categoria="crm", permitidos=("etiqueta", "origen_crm"), clave_negocio=("contacto_id", "etiqueta")
    ),
    "compra_iniciada": EventSpec(
        categoria="compra",
        permitidos=("orden_id", "monto", "moneda", "items"),
        clave_negocio=("orden_id",),
        valor_datos="monto",
    ),
    "recompra_confirmada": EventSpec(
        categoria="compra",
        requeridos=("orden_id",),
        permitidos=("orden_id", "monto", "moneda", "items"),
        clave_negocio=("orden_id",),
        valor_datos="monto",
    ),
    "reembolso_procesado": EventSpec(
        categoria="compra",
        requeridos=("orden_id",),
        permitidos=("orden_id", "monto", "moneda", "motivo"),
        clave_negocio=("orden_id", "monto"),
        valor_datos="monto",
    ),
    "acceso_concedido": EventSpec(
        categoria="comunidad",
        permitidos=("membresia_id", "plan", "vigencia_hasta", "plugin"),
        clave_negocio=("membresia_id",),
    ),
    "acceso_revocado": EventSpec(
        categoria="comunidad",
        permitidos=("membresia_id", "plan", "motivo"),
        clave_negocio=("membresia_id", "motivo"),
    ),
    "acceso_vencido": EventSpec(
        categoria="comunidad",
        permitidos=("membresia_id", "plan", "vigencia_hasta"),
        clave_negocio=("membresia_id",),
    ),
    "grupo_ingresado": EventSpec(
        categoria="comunidad",
        requeridos=("group_id",),
        permitidos=("group_id", "group_name", "via"),
        clave_negocio=("group_id",),
        nombre_datos="group_name",
        usa_alumno=True,
    ),
    "grupo_salida": EventSpec(
        categoria="comunidad",
        requeridos=("group_id",),
        permitidos=("group_id", "group_name", "motivo"),
        clave_negocio=("group_id",),
        nombre_datos="group_name",
        usa_alumno=True,
    ),
    "cohorte_cerrada": EventSpec(
        categoria="comunidad", permitidos=("motivo",), clave_negocio=("cohorte_slug",)
    ),
    "leccion_completada": EventSpec(
        categoria="curso",
        requeridos=("course_id", "leccion_id"),
        permitidos=("course_id", "leccion_id", "course_name", "leccion_nombre"),
        clave_negocio=("course_id", "leccion_id"),
        nombre_datos="leccion_nombre",
        usa_alumno=True,
    ),
    "tema_completado": EventSpec(
        categoria="curso",
        requeridos=("course_id", "tema_id"),
        permitidos=("course_id", "tema_id", "tema_nombre"),
        clave_negocio=("course_id", "tema_id"),
        nombre_datos="tema_nombre",
        usa_alumno=True,
    ),
    "quiz_completado": EventSpec(
        categoria="curso",
        requeridos=("quiz_id",),
        permitidos=("quiz_id", "quiz_name", "course_id", "aprobado", "puntaje"),
        clave_negocio=("quiz_id",),
        nombre_datos="quiz_name",
        usa_alumno=True,
    ),
    "curso_completado": EventSpec(
        categoria="curso",
        requeridos=("course_id",),
        permitidos=("course_id", "course_name", "progreso"),
        clave_negocio=("course_id",),
        nombre_datos="course_name",
        usa_alumno=True,
    ),
    "certificacion_emitida": EventSpec(
        categoria="curso",
        requeridos=("course_id",),
        permitidos=("course_id", "certificado_id", "certificado_nombre"),
        clave_negocio=("course_id", "certificado_id"),
        nombre_datos="certificado_nombre",
        usa_alumno=True,
    ),
    "tramo_ascendido": EventSpec(
        categoria="escalera", permitidos=("regla",), clave_negocio=("tramo_destino",), usa_alumno=True
    ),
    "tramo_descendido": EventSpec(
        categoria="escalera", permitidos=("regla", "motivo"), clave_negocio=("tramo_destino",), usa_alumno=True
    ),
    "referido_atribuido": EventSpec(
        categoria="escalera", permitidos=("referidor_id", "codigo"), clave_negocio=("referidor_id",)
    ),
    "usuario_purgado": EventSpec(
        categoria="privacidad", permitidos=("alcance", "base_legal"), clave_negocio=("usuario_ref",)
    ),
}


# ---------------------------------------------------------------------------
# Validación del envelope
# ---------------------------------------------------------------------------


@dataclass
class Discard:
    """Un campo que llegó y no se reenvía. Se cuenta, no se ignora en silencio."""

    campo: str
    motivo: str  # campo_no_declarado | pii


@dataclass
class Envelope:
    evento: str
    spec: EventSpec
    evento_id: str
    ocurrido_en: str
    emisor: str
    sitio: dict[str, Any]
    datos: dict[str, Any]
    consentimiento: str
    usuario_wp_id: int | None
    visita_id: str | None
    tramo: dict[str, Any]
    producto: dict[str, Any]
    cohorte: dict[str, Any]
    grupo: dict[str, Any]
    origen: dict[str, Any]
    pagina: dict[str, Any]
    contexto: dict[str, Any]
    descartes: list[Discard] = field(default_factory=list)

    @property
    def consultor_slug(self) -> str:
        return str(self.sitio.get("consultor_slug", "")).strip()

    @property
    def idsite_declarado(self) -> int | None:
        valor = self.sitio.get("idsite_matomo")
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def clave_negocio(self) -> str | None:
        """§5.3.2: identifica el hecho más allá del `evento_id`.

        WooCommerce dispara varios hooks por una misma compra; sin esto, una
        compra se cuenta tres veces. La clave se arma con los campos que el
        catálogo declara para el evento, así que dos `evento_id` distintos del
        mismo hecho colisionan a propósito.

        El alumno entra en la clave cuando el catálogo lo declara
        (`EventSpec.usa_alumno`): los eventos de progreso identifican el hecho
        por **(alumno, objeto)**, no por el objeto solo. Sin el alumno, el mismo
        curso/lección/grupo da la misma clave para todos, y el segundo alumno se
        descarta como `duplicate_business` — es decir, se pierde un evento, que
        es justo lo que el §5.3.2 no puede permitir. Y cuando el evento no trae
        alumno (visitante sin sesión) la clave queda incompleta y no se arma:
        mejor sin deduplicación por negocio que un falso duplicado.
        """
        campos = list(self.spec.clave_negocio)
        if not campos:
            return None
        if self.spec.usa_alumno and "usuario_wp_id" not in campos:
            campos = ["usuario_wp_id", *campos]
        partes: list[str] = []
        for campo in campos:
            if campo == "usuario_wp_id":
                valor: Any = self.usuario_wp_id
            elif campo == "tramo_destino":
                valor = self.tramo.get("destino")
            elif campo == "cohorte_slug":
                valor = self.cohorte.get("slug")
            else:
                valor = self.datos.get(campo)
            if valor in (None, ""):
                return None
            partes.append(f"{campo}={valor}")
        return "|".join(partes)


def _texto(valor: Any, campo: str, *,
           maximo: int = 200, obligatorio: bool = False) -> str | None:
    if valor in (None, ""):
        if obligatorio:
            raise ValidationError(f"falta `{campo}`")
        return None
    if not isinstance(valor, (str, int, float)):
        raise ValidationError(f"`{campo}` debe ser una cadena")
    texto = str(valor).strip()
    if not texto:
        if obligatorio:
            raise ValidationError(f"falta `{campo}`")
        return None
    return texto[:maximo]


def _objeto(valor: Any, campo: str) -> dict[str, Any]:
    if valor is None:
        return {}
    if not isinstance(valor, dict):
        raise ValidationError(f"`{campo}` debe ser un objeto")
    return valor


def _filtrar_sub(nombre: str, crudo: dict[str, Any], descartes: list[Discard]) -> dict[str, Any]:
    """Allow-list de un sub-objeto del envelope: lo no declarado se descarta."""
    permitidos = SUB_OK.get(nombre, frozenset())
    limpio: dict[str, Any] = {}
    for clave, valor in crudo.items():
        if clave not in permitidos:
            descartes.append(Discard(f"{nombre}.{clave}", "campo_no_declarado"))
            continue
        if clave.lower() in CLAVES_PII:
            descartes.append(Discard(f"{nombre}.{clave}", "pii"))
            continue
        if isinstance(valor, str) and _RE_EMAIL.match(valor.strip()):
            descartes.append(Discard(f"{nombre}.{clave}", "pii"))
            continue
        limpio[clave] = valor
    return limpio


def parse_envelope(body: dict[str, Any]) -> Envelope:
    """Valida el envelope §5.1 y devuelve el evento tipado.

    Allow-list estricta: lo que no está declarado se descarta (y se cuenta). Un
    campo obligatorio que falta sí es un rechazo: mejor un 400 visible que un
    evento sin identidad ni fecha.
    """
    if not isinstance(body, dict):
        raise ValidationError("el cuerpo debe ser un objeto")

    descartes: list[Discard] = []
    for clave, valor in body.items():
        if clave not in ENVELOPE_OK:
            descartes.append(Discard(clave, "campo_no_declarado"))
            continue
        if clave.lower() in CLAVES_PII or (
            isinstance(valor, str) and _RE_EMAIL.match(valor.strip())
        ):
            descartes.append(Discard(clave, "pii"))

    nombre = _texto(body.get("evento"), "evento", maximo=64, obligatorio=True) or ""
    if nombre not in CATALOGO:
        raise ValidationError(
            f"evento desconocido: {nombre!r} (catálogo §5.2: {', '.join(sorted(CATALOGO))})"
        )
    spec = CATALOGO[nombre]

    version = body.get("evento_version")
    try:
        version_int = int(str(version))
    except (TypeError, ValueError) as exc:
        raise ValidationError("`evento_version` debe ser un entero") from exc
    if version_int > EVENTO_VERSION_MAX:
        raise ValidationError(
            f"evento_version={version_int} no soportada (máxima: {EVENTO_VERSION_MAX})"
        )

    event_id = _texto(body.get("evento_id"), "evento_id", maximo=200, obligatorio=True) or ""
    ocurrido_en = _texto(body.get("ocurrido_en"), "ocurrido_en", maximo=40, obligatorio=True) or ""

    emisor = str(body.get("emisor") or "").strip()
    if emisor not in EMISORES:
        raise ValidationError(f"`emisor` inválido: {emisor!r} (permitidos: {', '.join(EMISORES)})")

    consentimiento = str(body.get("consentimiento") or "").strip()
    if consentimiento not in CONSENTIMIENTOS:
        raise ValidationError(
            f"`consentimiento` inválido: {consentimiento!r} (permitidos: {', '.join(CONSENTIMIENTOS)})"
        )

    sitio = _filtrar_sub("sitio", _objeto(body.get("sitio"), "sitio"), descartes)
    if not str(sitio.get("consultor_slug", "")).strip():
        raise ValidationError("falta `sitio.consultor_slug`")

    usuario_wp_id: int | None = None
    if body.get("usuario_wp_id") not in (None, ""):
        try:
            usuario_wp_id = int(body["usuario_wp_id"])
        except (TypeError, ValueError) as exc:
            # Nunca email: si no es entero, no es un id de WordPress.
            raise ValidationError("`usuario_wp_id` debe ser un entero") from exc
        if usuario_wp_id <= 0:
            usuario_wp_id = None

    visita_id: str | None = None
    crudo_visita = body.get("visita_id")
    if isinstance(crudo_visita, str):
        candidato = crudo_visita.strip().lower()
        if len(candidato) == 16 and all(c in _HEX for c in candidato):
            visita_id = candidato
        else:
            descartes.append(Discard("visita_id", "tipo_invalido"))

    pagina = _filtrar_sub("pagina", _objeto(body.get("pagina"), "pagina"), descartes)
    if pagina.get("url") not in (None, ""):
        pagina["url"] = _texto(pagina["url"], "pagina.url", maximo=1500)

    datos_crudos = _objeto(body.get("datos"), "datos")
    datos: dict[str, Any] = {}
    for clave, valor in datos_crudos.items():
        if clave not in spec.permitidos:
            motivo = "pii" if clave.lower() in CLAVES_PII else "campo_no_declarado"
            descartes.append(Discard(f"datos.{clave}", motivo))
            continue
        if isinstance(valor, str) and _RE_EMAIL.match(valor.strip()):
            descartes.append(Discard(f"datos.{clave}", "pii"))
            continue
        datos[clave] = valor

    faltantes = [campo for campo in spec.requeridos if datos.get(campo) in (None, "")]
    if faltantes:
        raise ValidationError(
            f"datos incompletos para {nombre}: falta {', '.join(faltantes)}"
        )

    return Envelope(
        evento=nombre,
        spec=spec,
        evento_id=event_id,
        ocurrido_en=ocurrido_en,
        emisor=emisor,
        sitio=sitio,
        datos=datos,
        consentimiento=consentimiento,
        usuario_wp_id=usuario_wp_id,
        visita_id=visita_id,
        tramo=_filtrar_sub("tramo", _objeto(body.get("tramo"), "tramo"), descartes),
        producto=_filtrar_sub("producto", _objeto(body.get("producto"), "producto"), descartes),
        cohorte=_filtrar_sub("cohorte", _objeto(body.get("cohorte"), "cohorte"), descartes),
        grupo=_filtrar_sub("grupo", _objeto(body.get("grupo"), "grupo"), descartes),
        origen=_filtrar_sub("origen", _objeto(body.get("origen"), "origen"), descartes),
        pagina=pagina,
        contexto=_filtrar_sub("contexto", _objeto(body.get("contexto"), "contexto"), descartes),
        descartes=descartes,
    )


# ---------------------------------------------------------------------------
# Traducción a Matomo (§5.4)
# ---------------------------------------------------------------------------


def matomo_datetime(raw: Any) -> str | None:
    """`ocurrido_en` (ISO 8601) -> `cdt` de Matomo (`Y-m-d H:i:s`, UTC)."""
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValidationError("ocurrido_en debe ser una cadena ISO 8601")
    candidato = raw.strip().replace("Z", "+00:00")
    try:
        parseado = datetime.fromisoformat(candidato)
    except ValueError as exc:
        raise ValidationError("ocurrido_en no es ISO 8601 válido") from exc
    if parseado.tzinfo is None:
        parseado = parseado.replace(tzinfo=timezone.utc)
    return parseado.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _entero_clp(raw: Any, campo: str) -> str:
    """`e_v` de Matomo no acepta texto: se valida que sea numérico.

    Los montos de este producto son enteros en CLP (§5.4 y la convención de
    precios), así que se normaliza a entero sin decimales.
    """
    try:
        valor = Decimal(str(raw))
    except (InvalidOperation, TypeError) as exc:
        raise ValidationError(f"{campo} debe ser numérico") from exc
    if not valor.is_finite():
        raise ValidationError(f"{campo} debe ser finito")
    if valor < 0:
        raise ValidationError(f"{campo} no puede ser negativo")
    entero = valor.to_integral_value()
    return f"{entero:.0f}"


def _url_del_evento(env: Envelope, dominio: str) -> str:
    """`url` de Matomo. Obligatoria en la API y ausente del envelope §5.1.

    Se resuelve en este orden: la página real (extensión `pagina.url` que el
    salto de WordPress sí conoce), el dominio del inquilino, y por último se
    rechaza el evento — antes que inventar una URL que ensucie el informe.
    """
    candidata = env.pagina.get("url")
    if isinstance(candidata, str) and candidata.strip().lower().startswith(("http://", "https://")):
        return candidata.strip()
    if dominio:
        return f"https://{dominio.strip().rstrip('/')}/"
    raise ValidationError("falta `pagina.url` y `sitio.dominio`: Matomo exige una URL")


def _nombre_evento(env: Envelope) -> str | None:
    """`e_n` del §5.4: `producto.slug:cohorte.slug` y, si no hay, el dato propio."""
    producto = str(env.producto.get("slug") or "").strip()
    cohorte = str(env.cohorte.get("slug") or "").strip()
    if producto and cohorte:
        return f"{producto}:{cohorte}"[:200]
    if producto:
        return producto[:200]
    if env.spec.nombre_datos:
        valor = env.datos.get(env.spec.nombre_datos)
        if valor not in (None, ""):
            return str(valor)[:200]
    return None


def _dimensiones(env: Envelope, mapa: dict[str, int]) -> dict[str, str]:
    """Dimensiones personalizadas del data layer map (§5.4 / `data_layer_map.yaml`).

    Los ids los administra Matomo (T2.9): entran por configuración, nunca
    cableados, y si el mapa está vacío no se manda ninguna.
    """
    valores = {
        "producto_slug": env.producto.get("slug"),
        "cohorte_slug": env.cohorte.get("slug"),
        "tramo_id": env.tramo.get("destino"),
        "consultor_slug": env.sitio.get("consultor_slug"),
        "evento_id": env.evento_id,
    }
    params: dict[str, str] = {}
    for nombre, valor in valores.items():
        dimension = mapa.get(nombre)
        if not dimension or valor in (None, ""):
            continue
        params[f"dimension{int(str(dimension))}"] = str(valor)[:255]
    return params


def build_tracking_params(
    *,
    env: Envelope,
    idsite: int,
    dominio: str,
    token_auth: str | None,
    client_ip: str | None,
    client_ua: str | None,
    dimensiones: dict[str, int] | None = None,
    ahora: str,
) -> dict[str, str]:
    """Cuerpo de la llamada a `matomo.php` para un evento del catálogo."""
    params: dict[str, str] = {
        # --- §5.4: fijos -----------------------------------------------------
        "idsite": str(idsite),
        "rec": "1",
        # `ca=1` evita que el evento se registre como page view fantasma.
        "ca": "1",
        "apiv": "1",
        "send_image": "0",  # Matomo responde 204 en vez de un GIF
        "e_c": env.spec.categoria,
        "e_a": env.evento,
        "url": _url_del_evento(env, dominio),
    }

    nombre = _nombre_evento(env)
    if nombre:
        params["e_n"] = nombre

    if env.spec.valor_datos:
        crudo = env.datos.get(env.spec.valor_datos)
        if crudo not in (None, ""):
            params["e_v"] = _entero_clp(crudo, f"datos.{env.spec.valor_datos}")

    # --- Identidad -----------------------------------------------------------
    # `uid` solo con consentimiento otorgado (§5.4) y nunca con el email: es el
    # id de WordPress, `wp:<id>`.
    if env.consentimiento == CONSENTIMIENTO_OTORGADO and env.usuario_wp_id:
        params["uid"] = f"wp:{env.usuario_wp_id}"
    if env.visita_id:
        params["_id"] = env.visita_id

    # --- Contexto ------------------------------------------------------------
    # `cdt` exige token_auth en Matomo. Sin token se omite y el evento
    # queda con la hora de reenvío (degradación visible).
    # `cip` NO se usa: Matomo 5.14 rechaza 400 toda IP pasada por `cip`,
    # incluso con token `write` y direcciones públicas. La IP queda como
    # la del contenedor, que es la degradación documentada en el README.
    cdt = matomo_datetime(env.ocurrido_en) or matomo_datetime(ahora)
    if cdt:
        params["cdt"] = cdt
    # `cip` deshabilitado: Matomo 5.14 lo rechaza (400) para toda IP.
    # if client_ip:
    #     params["cip"] = client_ip
    if client_ua:
        params["ua"] = client_ua[:500]

    referrer = env.pagina.get("referrer") or env.origen.get("referrer")
    if isinstance(referrer, str) and referrer.strip():
        params["urlref"] = referrer.strip()[:1500]

    # --- Atribución (UTM del §5.4) -------------------------------------------
    for clave_origen, destino in (
        ("utm_campaign", "mtm_campaign"),
        ("utm_source", "mtm_source"),
        ("utm_medium", "mtm_medium"),
        ("utm_content", "mtm_content"),
        ("utm_term", "mtm_keyword"),
    ):
        valor = env.origen.get(clave_origen)
        if isinstance(valor, str) and valor.strip():
            params[destino] = valor.strip()[:200]

    # --- Dimensiones personalizadas ------------------------------------------
    params.update(_dimensiones(env, dimensiones or {}))

    if token_auth and ("cip" in params or "cdt" in params):
        params["token_auth"] = token_auth

    return params
