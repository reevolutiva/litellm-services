"""Deduplicación de eventos por `event_id`.

El criterio "los eventos no se pierden ni se duplican" se sostiene en tres capas:

1. **El origen** (plugin de WordPress) marca el hecho ya enviado en meta del
   pedido/usuario, así que un hook que dispara dos veces no genera dos eventos.
2. **Acá**: una caché TTL por `event_id`. Un reintento del mismo evento (el
   `fetch` del navegador reenviando, `wp_remote_post` reintentando, un
   `sendBeacon` duplicado al cerrar la pestaña) no reenvía a Matomo.
3. **Métricas**: `duplicate` se cuenta aparte, así que la deduplicación es
   observable y no una caja negra.

El registro es en dos tiempos —`reserve` antes de reenviar, `commit` solo si
Matomo aceptó, `release` si falló— porque marcar "visto" antes de reenviar
convertiría un `502` en una pérdida silenciosa: el reintento del cliente
encontraría la clave reservada y no habría evento nunca.

La caché es en memoria y por réplica: con N réplicas, un `event_id` que cambie
de pod puede reenviarse. Por eso la garantía fuerte vive en la capa 1 y esta es
la segunda línea. Si se necesita deduplicación exacta entre réplicas, el camino
es una caché compartida (Redis) — está anotado en `apps/tracking/README.md`.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict


class TtlDedupe:
    """Caché TTL acotada, segura para varios hilos, sin estado en disco."""

    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = max(int(ttl_seconds), 1)
        self._max = max(int(max_entries), 1)
        self._entries: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()
        self._hits = 0

    def reserve(self, key: str) -> bool:
        """Reserva la clave. False si ya estaba vista (o en vuelo)."""
        ahora = time.time()
        with self._lock:
            self._evict(ahora)
            if self._entries.get(key) is not None:
                self._entries.move_to_end(key)
                self._hits += 1
                return False
            self._entries[key] = ahora
            self._entries.move_to_end(key)
            self._trim()
            return True

    def commit(self, key: str) -> None:
        """Confirma el envío: la clave queda marcada por el TTL completo."""
        with self._lock:
            self._entries[key] = time.time()
            self._entries.move_to_end(key)

    def release(self, key: str) -> None:
        """Libera la reserva porque el reenvío falló: el reintento debe pasar."""
        with self._lock:
            self._entries.pop(key, None)

    def _trim(self) -> None:
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    def _evict(self, ahora: float) -> None:
        while self._entries:
            clave, ts = next(iter(self._entries.items()))
            if ahora - ts > self._ttl:
                self._entries.popitem(last=False)
            else:
                break

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits
