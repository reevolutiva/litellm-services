# Headroom — proxy de compresión de contexto (sidecar de LiteLLM).
#
# Fuente: PyPI, paquete público `headroom-ai[proxy]` (upstream:
# github.com/headroomlabs-ai/headroom). No hay imagen oficial publicada, así que la
# construimos nosotros — y la construimos **multi-arquitectura**, porque `imac27` es
# amd64 y `produccion` es arm64: la misma etiqueta tiene que correr en los dos.
#
# La versión está **pineada a la que corre hoy en el host legacy** (0.34.0). PyPI ya
# publicó 0.39.1: subirla es un cambio de comportamiento y va en su propio PR, no
# colado dentro de un fix de disponibilidad.
#
# Este Dockerfile es el que estaba en `/opt/litellm/headroom/Dockerfile` de `frontdoor`,
# tal cual: era correcto y reconstruible, solo vivía fuera del GitOps.
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && pip install --no-cache-dir "headroom-ai[proxy]==0.34.0" \
 && apt-get purge -y build-essential \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

EXPOSE 8787
ENV HEADROOM_TELEMETRY=off
CMD ["headroom", "proxy", "--host", "0.0.0.0", "--port", "8787"]
