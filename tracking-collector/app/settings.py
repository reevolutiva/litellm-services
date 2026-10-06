"""Configuración del contenedor de tracking server-side.

Todo lo que cambia entre entornos entra por acá; todo lo que es secreto entra
por archivo montado (`/etc/tracking/secrets/…`, materializado desde AKV por
External Secrets) y no por variable de entorno, porque las variables de entorno
se ven en `kubectl describe pod`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

VERSION = os.environ.get("TRACKING_VERSION", "0.1.1")

# Dimensiones personalizadas del §5.4, con los ids del data layer map
# (`plantillas/data_layer_map.yaml`, T1.1). Los ids son del destino y los
# administra Matomo (T2.9): esto es el valor por defecto y se cambia con
# `MATOMO_DIMENSIONS` sin tocar código ni imagen.
DIMENSIONES_POR_DEFECTO: dict[str, int] = {
    "producto_slug": 1,
    "cohorte_slug": 2,
    "tramo_id": 3,
    "consultor_slug": 4,
    "evento_id": 5,
}


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "si", "sí")


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - arranque con config inválida
        raise ConfigError(f"{name}={raw!r} no es un entero") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover
        raise ConfigError(f"{name}={raw!r} no es un número") from exc


def _dimensiones(name: str) -> dict[str, int]:
    """`MATOMO_DIMENSIONS` = JSON {nombre: id}, o `none` para no mandar ninguna.

    Se configura porque los ids de las dimensiones los asigna Matomo al
    crearlas (T2.9): si el destino cambia, cambia esta variable, no el código.
    """
    raw = os.environ.get(name)
    if raw is None:
        return dict(DIMENSIONES_POR_DEFECTO)
    if raw.strip().lower() in ("", "none", "off", "0", "no"):
        return {}
    try:
        mapa = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} no es JSON válido: {exc}") from exc
    if not isinstance(mapa, dict):
        raise ConfigError(f"{name} debe ser un objeto {{nombre: id}}")
    salida: dict[str, int] = {}
    for clave, valor in mapa.items():
        try:
            salida[str(clave)] = int(str(valor))
        except ValueError as exc:
            raise ConfigError(f"{name}: {clave}={valor!r} no es un entero") from exc
    return salida


class ConfigError(RuntimeError):
    """Configuración inválida: el contenedor no debe arrancar con esto."""


@dataclass(frozen=True)
class Site:
    """Un inquilino: un WordPress de consultor mapeado a un `idsite` de Matomo.

    El `idsite` vive **acá** y no en el payload: el aislamiento entre inquilinos
    (G6, §7 del SSOT) es una decisión del contenedor, no algo que el emisor
    pueda declarar. Si el evento trae un `sitio.idsite_matomo` distinto, se
    registra la discrepancia y manda este valor.
    """

    slug: str
    idsite: int
    dominio: str = ""
    token: str | None = None

    def autorizado(self, token_recibido: str | None) -> bool:
        import hmac

        if not self.token:
            # Sin token configurado para el sitio: se acepta (modo degradado),
            # salvo que REQUIRE_SITE_TOKEN esté activo.
            return not REQUIRE_SITE_TOKEN
        if not token_recibido:
            return False
        return hmac.compare_digest(self.token, token_recibido)


@dataclass
class Settings:
    matomo_url: str
    matomo_token: str | None
    sites: dict[str, Site]
    dimensiones: dict[str, int] = field(default_factory=lambda: dict(DIMENSIONES_POR_DEFECTO))
    require_site_token: bool = True
    dedupe_ttl_seconds: int = 86400
    dedupe_max_entries: int = 50000
    forward_timeout_seconds: float = 5.0
    forward_retries: int = 2
    request_max_bytes: int = 64 * 1024
    shadow_mode: bool = False
    version: str = VERSION
    warnings: list[str] = field(default_factory=list)

    def site(self, slug: str) -> Site | None:
        return self.sites.get(slug)


def _load_sites(path: Path, tokens_path: Path | None) -> dict[str, Site]:
    if not path.exists():
        raise ConfigError(f"no existe el archivo de sitios {path}")
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} no es JSON válido: {exc}") from exc

    entries = raw.get("sites", raw)
    if not isinstance(entries, dict) or not entries:
        raise ConfigError(f"{path} no declara ningún sitio")

    tokens: dict[str, str] = {}
    if tokens_path is not None and tokens_path.exists():
        try:
            tokens = json.loads(tokens_path.read_text())
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{tokens_path} no es JSON válido: {exc}") from exc
        if not isinstance(tokens, dict):
            raise ConfigError(f"{tokens_path} debe ser un objeto {{slug: token}}")

    sites: dict[str, Site] = {}
    for slug, cfg in entries.items():
        if not isinstance(cfg, dict):
            raise ConfigError(f"sitio {slug!r}: se esperaba un objeto")
        try:
            idsite = int(str(cfg["idsite"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"sitio {slug!r}: falta `idsite` entero") from exc
        # `dominio` es el nombre del §5.1; `base_url` se acepta por
        # compatibilidad con el primer borrador de este ConfigMap.
        dominio = str(cfg.get("dominio") or cfg.get("base_url") or "").strip()
        sites[slug] = Site(
            slug=slug,
            idsite=idsite,
            dominio=dominio.rstrip("/"),
            token=tokens.get(slug),
        )
    return sites


def load_settings() -> Settings:
    matomo_url = os.environ.get("MATOMO_URL", "").rstrip("/")
    if not matomo_url:
        raise ConfigError("falta MATOMO_URL")

    sites_file = Path(os.environ.get("SITES_FILE", "/etc/tracking/sites.json"))
    tokens_env = os.environ.get("SITE_TOKENS_FILE")
    tokens_file = Path(tokens_env) if tokens_env else None

    global REQUIRE_SITE_TOKEN  # noqa: PLW0603 - flag de módulo leído por Site.autorizado
    REQUIRE_SITE_TOKEN = _bool("REQUIRE_SITE_TOKEN", True)

    matomo_token = os.environ.get("MATOMO_TOKEN") or None
    matomo_token_file = os.environ.get("MATOMO_TOKEN_FILE")
    if matomo_token_file and Path(matomo_token_file).exists():
        matomo_token = Path(matomo_token_file).read_text().strip() or None

    settings = Settings(
        matomo_url=matomo_url,
        matomo_token=matomo_token,
        sites=_load_sites(sites_file, tokens_file),
        dimensiones=_dimensiones("MATOMO_DIMENSIONS"),
        require_site_token=REQUIRE_SITE_TOKEN,
        dedupe_ttl_seconds=_int("DEDUPE_TTL_SECONDS", 86400),
        dedupe_max_entries=_int("DEDUPE_MAX_ENTRIES", 50000),
        forward_timeout_seconds=_float("FORWARD_TIMEOUT_SECONDS", 5.0),
        forward_retries=_int("FORWARD_RETRIES", 2),
        request_max_bytes=_int("REQUEST_MAX_BYTES", 64 * 1024),
        shadow_mode=_bool("SHADOW_MODE", False),
        version=VERSION,
    )

    # Degradación explícita, no silenciosa: sin token de Matomo no se puede
    # enviar `cip` ni `cdt`, así que el evento queda con la IP del contenedor y
    # la fecha de reenvío. Se registra en el arranque para que sea visible.
    if not matomo_token:
        settings.warnings.append(
            "sin MATOMO_TOKEN: se envían los eventos sin cip/cdt (IP y fecha no son las del usuario)"
        )
    if not settings.dimensiones:
        settings.warnings.append(
            "MATOMO_DIMENSIONS vacío: los eventos no llevan dimensiones personalizadas "
            "(se pierde la auditoría de `evento_id` en el informe)"
        )
    sin_token = [s for s, v in settings.sites.items() if not v.token]
    if settings.require_site_token and sin_token:
        settings.warnings.append(
            "sitios sin token de ingreso (se rechazarán): " + ", ".join(sorted(sin_token))
        )
    sin_dominio = [s for s, v in settings.sites.items() if not v.dominio]
    if sin_dominio:
        settings.warnings.append(
            "sitios sin `dominio` (dependen de `pagina.url` en cada evento): "
            + ", ".join(sorted(sin_dominio))
        )
    return settings
