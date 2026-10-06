#!/usr/bin/env bash
# Corre los tests del contenedor de tracking dentro de su propia imagen.
#
# Por qué dentro de la imagen y no en el host: el contenedor no tiene
# dependencias de desarrollo en la máquina del operador, y así se prueba con
# exactamente las versiones que van a producción (requirements.txt), no las que
# tenga instaladas quien corre el test.
#
#   ./apps/tracking/collector/run-tests.sh
set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGEN="${IMAGEN:-registry.coyote-paridae.ts.net/tracking-collector:0.1.2}"

docker build -t "$IMAGEN" "$AQUI"
# `--rm` y `-p no:cacheprovider`: el contenedor corre como root, así que sin
# esto deja un `.pytest_cache` que el usuario del host no puede borrar.
docker run --rm --user root \
  -v "$AQUI:/app" -w /app "$IMAGEN" \
  sh -c "pip install -q -r requirements-dev.txt && python -m pytest -q -p no:cacheprovider"
echo "=== tests OK ==="
