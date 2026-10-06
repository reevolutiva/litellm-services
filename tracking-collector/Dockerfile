# =============================================================================
# Imagen del contenedor de tracking server-side (T0.6).
#
# Es runtime puro, igual que la de familey: el código va en la imagen (no hay
# PVC, no hay estado) y los secretos llegan en runtime desde AKV vía ESO.
#
# Multi-arquitectura a propósito: `imac27` es amd64 y `produccion` es arm64.
# Corre sin root y sin escrituras al sistema de archivos.
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TRACKING_VERSION=0.1.1

WORKDIR /app

# Las dependencias primero: la capa se cachea y un cambio de código no
# reinstala nada.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios y sin shell de login.
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin tracking
USER 10001

EXPOSE 8080

# `--factory` porque la app se construye con la configuración ya validada: si
# falta MATOMO_URL o el archivo de sitios está mal, el pod no arranca en vez de
# aceptar eventos y perderlos.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080", \
     "--no-access-log"]
