"""Reenvío a la HTTP Tracking API de Matomo.

Un evento del data layer = una llamada a `matomo.php`. El contenedor es
síncrono a propósito: si Matomo no aceptó el evento, el llamador recibe 502 y
**puede reintentar** (el plugin de WordPress reprograma el envío, el navegador
reenvía con `keepalive`). Aceptar en silencio y perder el evento sería peor que
devolver un error.
"""

from __future__ import annotations

import asyncio

import httpx

from .events import MATOMO_TRACKER_PATH


class ForwardError(RuntimeError):
    """Matomo no aceptó el evento tras agotar los reintentos."""

    def __init__(self, status: int | None, detail: str) -> None:
        super().__init__(f"matomo status={status} detail={detail}")
        self.status = status
        self.detail = detail


class MatomoForwarder:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 5.0,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = f"{base_url.rstrip('/')}{MATOMO_TRACKER_PATH}"
        self._timeout = timeout
        self._retries = max(int(retries), 0)
        self._client = client or httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": "reev-tracking-collector/0.1"}
        )
        self._own_client = client is None

    async def aclose(self) -> None:
        if self._own_client:
            await self._client.aclose()

    async def forward(self, params: dict[str, str]) -> int:
        """Envía el evento. Devuelve el status de Matomo o levanta `ForwardError`."""
        intento = 0
        ultimo_status: int | None = None
        ultimo_detalle = "sin intentos"
        while intento <= self._retries:
            try:
                respuesta = await self._client.post(
                    self._url,
                    data=params,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=self._timeout,
                )
            except httpx.HTTPError as exc:
                ultimo_status, ultimo_detalle = None, f"{type(exc).__name__}: {exc}"
            else:
                if 200 <= respuesta.status_code < 300:
                    return respuesta.status_code
                ultimo_status = respuesta.status_code
                ultimo_detalle = respuesta.text[:300]
            intento += 1
            if intento <= self._retries:
                # Backoff acotado: 0.2s, 0.4s, 0.8s...
                await asyncio.sleep(0.2 * (2 ** (intento - 1)))
        raise ForwardError(ultimo_status, ultimo_detalle)

    async def probe(self, token_auth: str | None = None) -> tuple[bool, str]:
        """Prueba de vida contra Matomo (para `/readyz?probe=1`).

        Con `token_auth` configurado la prueba es **de verdad**: Matomo devuelve
        401 a la API sin token (el acceso anónimo viene deshabilitado), así que
        un 401 con token en mano sí es una credencial rechazada y tiene que
        verse como `not_ready`. Sin token, el 401 solo dice que Matomo contesta.
        """
        params = {"module": "API", "method": "API.getMatomoVersion", "format": "json"}
        if token_auth:
            params["token_auth"] = token_auth
        try:
            respuesta = await self._client.get(
                f"{self._url.rsplit('/matomo.php', 1)[0]}/index.php",
                timeout=self._timeout,
                params=params,
            )
        except httpx.HTTPError as exc:
            return False, f"{type(exc).__name__}: {exc}"

        if not token_auth:
            # Sin credencial no se puede distinguir "Matomo está" de "Matomo me
            # deja entrar": cualquier respuesta por debajo de 500 es vida.
            return (200 <= respuesta.status_code < 500), f"status={respuesta.status_code} (sin token_auth)"
        if respuesta.status_code == 401:
            return False, "status=401: Matomo rechazó el token_auth"
        return (200 <= respuesta.status_code < 500), f"status={respuesta.status_code}"
