"""Métricas en formato de exposición de Prometheus, sin dependencias.

El clúster ya corre Prometheus (`apps/monitoring`), pero sin CRDs de
ServiceMonitor: el enganche a ese Prometheus es una config de scrape (está
anotado en `apps/tracking/README.md`). Mientras tanto, `/metrics` es lo que hace
que "no se pierden ni se duplican" sea medible en vez de una afirmación.
"""

from __future__ import annotations

import threading
from collections import defaultdict


def _fmt_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    partes = ",".join(f'{clave}="{_escape(valor)}"' for clave, valor in sorted(labels.items()))
    return "{" + partes + "}"


def _escape(valor: str) -> str:
    return str(valor).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class Metrics:
    """Contadores, sumas/observaciones y gauges con etiquetas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, str], float] = defaultdict(float)
        self._observations: dict[tuple[str, str], tuple[float, float]] = defaultdict(
            lambda: (0.0, 0.0)
        )
        self._gauges: dict[tuple[str, str], float] = {}
        self._help: dict[str, str] = {}
        self._types: dict[str, str] = {}

    def _declare(self, name: str, help_text: str, tipo: str) -> None:
        self._help.setdefault(name, help_text)
        self._types[name] = tipo

    def inc(self, name: str, labels: dict[str, str] | None = None, value: float = 1.0,
            help_text: str = "") -> None:
        self._declare(name, help_text or name, "counter")
        with self._lock:
            self._counters[(name, _fmt_labels(labels))] += value

    def observe(self, name: str, seconds: float, labels: dict[str, str] | None = None,
                help_text: str = "") -> None:
        self._declare(name, help_text or name, "summary")
        with self._lock:
            total, cuenta = self._observations[(name, _fmt_labels(labels))]
            self._observations[(name, _fmt_labels(labels))] = (total + seconds, cuenta + 1)

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None,
              help_text: str = "") -> None:
        self._declare(name, help_text or name, "gauge")
        with self._lock:
            self._gauges[(name, _fmt_labels(labels))] = value

    def render(self) -> str:
        with self._lock:
            nombres = sorted({name for name, _ in self._counters}
                             | {name for name, _ in self._observations}
                             | {name for name, _ in self._gauges})
            lineas: list[str] = []
            for nombre in nombres:
                tipo = self._types.get(nombre, "gauge")
                lineas.append(f"# HELP {nombre} {self._help.get(nombre, nombre)}")
                lineas.append(f"# TYPE {nombre} {tipo}")
                for (n, etiquetas), valor in sorted(self._counters.items()):
                    if n == nombre:
                        lineas.append(f"{nombre}{etiquetas} {_num(valor)}")
                for (n, etiquetas), (total, cuenta) in sorted(self._observations.items()):
                    if n == nombre:
                        lineas.append(f"{nombre}_sum{etiquetas} {_num(total)}")
                        lineas.append(f"{nombre}_count{etiquetas} {_num(cuenta)}")
                for (n, etiquetas), valor in sorted(self._gauges.items()):
                    if n == nombre:
                        lineas.append(f"{nombre}{etiquetas} {_num(valor)}")
        return "\n".join(lineas) + "\n"


def _num(valor: float) -> str:
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return f"{valor:.6f}".rstrip("0").rstrip(".") if isinstance(valor, float) else str(valor)


# Nombres centralizados: las métricas se citan en el README y en los tests.
METRIC_EVENTS = "reev_tracking_events_total"
METRIC_FORWARD = "reev_tracking_forward_attempts_total"
METRIC_FORWARD_SECONDS = "reev_tracking_forward_seconds"
METRIC_DEDUPE_ENTRIES = "reev_tracking_dedupe_entries"
METRIC_BUILD_INFO = "reev_tracking_build_info"
# El descarte de campos no declarados y de PII (§5.3.3 / §8.4) tiene que ser
# observable: si no, "se descarta" es una afirmación sin evidencia.
METRIC_DISCARDED = "reev_tracking_discarded_fields_total"
# Duplicados detectados por clave de negocio y no por `evento_id` (§5.3.2): es la
# métrica que delata el caso WooCommerce (tres hooks, una compra).
METRIC_BUSINESS_DUPLICATES = "reev_tracking_business_duplicates_total"
