# Plan completo — handoff para mejora de rendimiento UI LiteLLM

## Contexto

**Issue**: https://github.com/reevolutiva/litellm-services/issues/7
**Título**: UI lenta: los estáticos viajan sin comprimir ni cachear (3,3× de bytes)

**Problema**: La UI de LiteLLM en `https://litellm.coyote-paridae.ts.net/ui/` tarda minutos en cargar desde Chile (el pod corre en Arizona). Hay dos defectos propios del stack que multiplican el costo de cada viaje:

| ID | Defecto | Evidencia |
|----|---------|-----------|
| C1 | Assets estáticos JS/CSS viajan sin comprimir | `content-encoding` ausente en 100% de respuestas pese a `Accept-Encoding: gzip, br, zstd` |
| C2 | Nada se cachea | `cache-control` ausente en 100% de respuestas; los chunks de Next.js llevan hash (inmutables) |

**Causa dominante**: geografía + transporte (documentado en `reevolutiva/reevolutiva-infra`, `docs/topologia-y-rendimiento-tailnet.md`). Pero C1 y C2 agravan el problema: con gzip los 949 KB de JS pasarían a ~285 KB (3,3× menos bytes).

## Lo ya hecho

1. **Evidencia 2026-10-13** (`docs/evidencia/2026-10-13-ui-rendimiento/`): HAR de 34 requests desde dispositivo real (Pixel 10, Android 16, Chrome 155). 0/34 con `content-encoding`, 0/34 con `cache-control`. Mediana TTFB: 82,2 s. Mediana velocidad: 0,16 KB/s.

2. **Evidencia 2026-10-14** (`docs/evidencia/2026-10-14-ui-compresion-cache/`):
   - `README.md`: descripción de mediciones con `curl` y conclusiones.
   - `mediciones.json`: 6 chunks JS/CSS medidos con `Accept-Encoding: gzip, br, zstd`. Todos devuelven 200, `content_encoding: null`, `cache_control: null`. Tamaños: 2 KB a 439 KB descargados sin comprimir.
   - PR #13 mergeado: docs de evidencia.

3. **Upgrade a v1.105.1** (PR #14 mergeado en `litellm-services`, PR #215 mergeado en `reevolutiva-infra`):
   - `litellm/litellm-helmrelease.yaml`: `image.tag: v1.105.1` (antes `v1.103.0-rc.1`)
   - `reevolutiva-infra/apps/litellm/litellm-helmrelease.yaml`: mismo cambio de tag para Flux
   - **v1.105.1 incluye gzip en respuestas buffereadas** (commit `eb10333`, PR #44052) para responses API grandes como `/user/daily/activity/aggregated`. Esto NO cubre los estáticos de Next.js (`/_next/static/*`), que pasan por otro mount en el proxy.

## Arquitectura actual del servicio LiteLLM

```
Cliente (Chile, Tailscale)
  │
  ▼
litellm.coyote-paridae.ts.net (MagicDNS)
  │
  ▼
Tailscale Ingress (proxy-class: produccion, nodo en Arizona)
  │ HTTPS terminado, reenvía HTTP plano
  ▼
Service litellm:4000
  │
  ▼
Pod litellm (ghcr.io/berriai/litellm:v1.105.1, 1 réplica, nodo produccion arm64)
  │
  ├── /ui/ → HTML inicial (sin comprimir, sin cache-control)
  ├── /litellm-asset-prefix/_next/static/* → assets JS/CSS (sin comprimir, sin cache-control)
  ├── /v1/* → API (ahora con gzip en respuestas buffereadas >500B en v1.105.1)
  └── Guardrail headroom (pre_call, api_base: http://headroom:8787)
       └── Pod headroom (actualmente ImagePullBackOff en vm-worker)
```

**Punto crítico**: el Ingress Tailscale termina TLS y reenvía directo al Service `litellm:4000`. No hay Traefik ni proxy HTTP en el camino que pueda comprimir o añadir headers. La compresión/caché tiene que salir del pod o de un proxy intermedio nuevo.

## Configuración relevante del HelmRelease

```yaml
# litellm/litellm-helmrelease.yaml (espejo en litellm-services,
# fuente de verdad en reevolutiva-infra/apps/litellm/)

image:
  repository: ghcr.io/berriai/litellm
  tag: v1.105.1

replicaCount: 1
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxSurge: 0
    maxUnavailable: 1

environmentSecrets:
  - litellm-env       # AKV: AZURE_AI_API_KEY, REDIS_PASSWORD, PROXY_MASTER_KEY, etc.
  - litellm-flags     # STORE_MODEL_IN_DB=false

env:
  - name: DISABLE_SCHEMA_UPDATE
    value: "true"

# NO hay variables de entorno para compresión de estáticos ni cache-control
# en esta configuración.
```

```yaml
# litellm/litellm-tailscale-ingress.yaml
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: litellm
      port:
        number: 4000
```

## Pasos siguientes (ordenados, ejecutables, verificables)

### Paso 1 — Verificar despliegue de v1.105.1 en el clúster

**Objetivo**: confirmar que Flux ya desplegó la nueva imagen y el pod está healthy.

```bash
export KUBECONFIG=~/.kube/config
kubectl -n litellm get pods -o wide
# Esperado: litellm-<hash> Running, image v1.105.1

# Si hay problemas:
kubectl -n litellm describe pod <pod-name>
kubectl -n litellm logs <pod-name> --tail 200
```

**Criterio de éxito**: pod `litellm-*` en estado `Running` con imagen `v1.105.1`.

---

### Paso 2 — Validación funcional básica

**Objetivo**: confirmar que la UI responde y no hay regresiones.

```bash
# UI principal
curl -sS -D - -o /dev/null https://litellm.coyote-paridae.ts.net/ui/

# Health
curl -sS https://litellm.coyote-paridae.ts.net/health/readiness

# Verificar headroom (estaba en ImagePullBackOff)
kubectl -n litellm get pods | grep headroom
```

**Criterio de éxito**: `/ui/` responde 200, `/health/readiness` 200. Documentar estado de headroom.

---

### Paso 3 — Re-medir assets estáticos con v1.105.1

**Objetivo**: comprobar si la nueva versión aplica compresión o cache-control a los estáticos de Next.js. Aunque el gzip añadido en v1.105.0 es solo para respuestas buffereadas de API, hay que verificarlo contra el endpoint real.

```bash
# Chunks de ejemplo (tomados del HTML de /ui/)
CHUNK="1kpvojzxb_2ce.js"
CSS="3146e697tym4_.css"

# Medir con Accept-Encoding
curl -sS -D - -H 'Accept-Encoding: gzip, br, zstd' \
  "https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/${CHUNK}" \
  2>&1 | head -20

# Medir tiempos y tamaño
curl -sS -w '\nttfb:%{time_starttransfer} total:%{time_total} size:%{size_download}\n' \
  -o /dev/null -H 'Accept-Encoding: gzip, br, zstd' \
  "https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/${CHUNK}"

# Verificar explícitamente si hay content-encoding
python3 -c "
import requests
r = requests.get(
    'https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/${CHUNK}',
    headers={'Accept-Encoding': 'gzip, br, zstd'},
    timeout=30
)
print('content-encoding:', r.headers.get('content-encoding'))
print('cache-control:', r.headers.get('cache-control'))
print('len:', len(r.content))
"
```

**Criterio de éxito**:
- Si `content-encoding: gzip` (o `br`) → **C1 resuelto**. Pasar a C2.
- Si `cache-control: public, max-age=31536000, immutable` → **C2 resuelto**.
- Si ambos ausentes → proceder al Paso 4.

---

### Paso 4 — Investigar opciones de configuración nativa en v1.105.1

**Objetivo**: determinar si la imagen v1.105.1 expone alguna variable de entorno o setting para habilitar compresión/caché en el mount de estáticos (`/_next`).

```bash
# Revisar variables de entorno documentadas para UI/proxy/compresión
# Fuentes a consultar:
# 1. https://docs.litellm.ai/docs/proxy/configs
# 2. https://github.com/BerriAI/litellm/blob/v1.105.1/README.md
# 3. Código fuente del proxy en v1.105.1:
#    https://github.com/BerriAI/litellm/tree/v1.105.1/litellm/proxy
```

**Preguntas a responder**:
- ¿Existe `LITELLM_UI_COMPRESSION` o similar?
- ¿El `GZipMiddleware` de Starlette se puede habilitar vía `general_settings`?
- ¿Hay un setting `ui_cache_control` o `static_headers`?
- ¿El mount de `/_next` en el proxy acepta headers adicionales?

**Criterio de éxito**: identificar al menos una vía de configuración nativa o confirmar que no existe.

---

### Paso 5a — Si existe configuración nativa: aplicarla

**Acción**: modificar `litellm-flags` Secret (o `values.env`) en `reevolutiva-infra/apps/litellm/` para activar la compresión/caché.

```yaml
# Ejemplo (a ajustar según lo descubierto en Paso 4):
apiVersion: v1
kind: Secret
metadata:
  name: litellm-flags
  namespace: litellm
type: Opaque
stringData:
  STORE_MODEL_IN_DB: "false"
  LITELLM_UI_GZIP: "true"                    # hipotético
  LITELLM_UI_CACHE_CONTROL: "public, max-age=31536000, immutable"  # hipotético
```

**Flujo**: rama → PR en `reevolutiva-infra` → merge → Flux reconcilia → re-medir (Paso 3).

---

### Paso 5b — Si NO existe configuración nativa: proxy intermedio con nginx

**Objetivo**: insertar un proxy HTTP delante de LiteLLM que añada compresión gzip y `cache-control: immutable` para `/_next/static/*`.

**Por qué nginx y no modificar la app**: el Ingress Tailscale no comprime (es proxy TCP con terminación TLS), la app LiteLLM no comprime estáticos, y nginx es ligero, bien conocido, y se despliega como sidecar o Deployment separado.

**Arquitectura propuesta**:

```
Cliente (Tailscale)
  │
  ▼
Ingress Tailscale (cambiar defaultBackend)
  │  defaultBackend → Service litellm-static-proxy:8080
  ▼
nginx (Deployment litellm-static-proxy)
  │  gzip on + add_header Cache-Control immutable para /_next/static/*
  │  proxy_pass → Service litellm:4000 para el resto
  ▼
Service litellm:4000 → Pod litellm
```

**Manifiestos a crear en `reevolutiva-infra/apps/litellm/`**:

```yaml
# 1. ConfigMap con nginx.conf
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: litellm-static-proxy-nginx
  namespace: litellm
data:
  nginx.conf: |
    worker_processes 1;
    events { worker_connections 1024; }
    http {
      include /etc/nginx/mime.types;
      default_type application/octet-stream;
      sendfile on;
      keepalive_timeout 65;

      # Compresión gzip para assets
      gzip on;
      gzip_comp_level 6;
      gzip_vary on;
      gzip_min_length 1024;
      gzip_proxied any;
      gzip_types
        text/plain
        text/css
        text/javascript
        application/javascript
        application/json
        application/xml
        text/xml
        image/svg+xml
        font/woff2;

      server {
        listen 8080;

        # Assets estáticos con hash: inmutables, cache larga
        location /litellm-asset-prefix/_next/static/ {
          proxy_pass http://litellm:4000;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
          add_header Cache-Control "public, max-age=31536000, immutable" always;
        }

        # Resto: pasar sin modificar
        location / {
          proxy_pass http://litellm:4000;
          proxy_set_header Host $host;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
        }
      }
    }
```

```yaml
# 2. Deployment de nginx
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: litellm-static-proxy
  namespace: litellm
spec:
  replicas: 1
  selector:
    matchLabels:
      app: litellm-static-proxy
  template:
    metadata:
      labels:
        app: litellm-static-proxy
    spec:
      containers:
        - name: nginx
          image: nginx:1.27-alpine
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          volumeMounts:
            - name: nginx-config
              mountPath: /etc/nginx/nginx.conf
              subPath: nginx.conf
      volumes:
        - name: nginx-config
          configMap:
            name: litellm-static-proxy-nginx
```

```yaml
# 3. Service para el proxy
---
apiVersion: v1
kind: Service
metadata:
  name: litellm-static-proxy
  namespace: litellm
spec:
  selector:
    app: litellm-static-proxy
  ports:
    - port: 8080
      targetPort: 8080
```

**Modificación al Ingress existente** (`litellm-tailscale-ingress.yaml`):

```yaml
# Cambiar defaultBackend de litellm:4000 a litellm-static-proxy:8080
spec:
  defaultBackend:
    service:
      name: litellm-static-proxy    # antes: litellm
      port:
        number: 8080                # antes: 4000
```

**Archivos en `reevolutiva-infra/apps/litellm/`**:
- `litellm-static-proxy-nginx-configmap.yaml` (nuevo)
- `litellm-static-proxy-deployment.yaml` (nuevo)
- `litellm-static-proxy-service.yaml` (nuevo)
- `litellm-tailscale-ingress.yaml` (modificado: cambiar defaultBackend)
- `kustomization.yaml` (modificado: añadir resources nuevos)

```yaml
# kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
resources:
  - litellm-helmrelease.yaml
  - litellm-tailscale-ingress.yaml
  - litellm-static-proxy-nginx-configmap.yaml
  - litellm-static-proxy-deployment.yaml
  - litellm-static-proxy-service.yaml
```

**Flujo**: rama → PR en `reevolutiva-infra` → merge → Flux reconcilia → re-medir (Paso 3).

---

### Paso 6 — Verificación post-cambio

**Después de aplicar la solución (5a o 5b), repetir exactamente las mismas mediciones del Paso 3**:

```bash
# Verificar content-encoding
python3 -c "
import requests
r = requests.get(
    'https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/1kpvojzxb_2ce.js',
    headers={'Accept-Encoding': 'gzip, br, zstd'},
    timeout=30
)
print('content-encoding:', r.headers.get('content-encoding'))
print('cache-control:', r.headers.get('cache-control'))
print('len:', len(r.content))
"
# Esperado: content-encoding: gzip, cache-control: public, max-age=31536000, immutable
```

**Verificación completa con varios chunks**:

```bash
# Lista de chunks para verificar (extraídos del HTML de /ui/)
CHUNKS="1kpvojzxb_2ce.js 28fzwmvhc4sv1.js 1xkrmcontg-7s.js 1kabwy23ggmhn.js 1kid9zr1--h6y.css 3146e697tym4_.css"

for chunk in $CHUNKS; do
  echo "=== $chunk ==="
  curl -sS -D - -o /dev/null -H 'Accept-Encoding: gzip, br, zstd' \
    "https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/$chunk" \
    2>&1 | grep -i 'content-encoding\|cache-control'
done
```

**Criterio de éxito final**:
- Todos los chunks JS/CSS devuelven `content-encoding: gzip` (o `br`)
- Todos los chunks bajo `/_next/static/` devuelven `cache-control: public, max-age=31536000, immutable`
- El contenido descargado es significativamente menor que el `content-length` (comprimido)
- Opcional: segunda captura DevTools sin "Disable cache" muestra assets servidos desde caché del navegador

---

### Paso 7 — Documentar y cerrar

1. **Actualizar `docs/evidencia/`** con una nueva carpeta `2026-10-XX-ui-post-fix/` que contenga:
   - `README.md` con mediciones antes/después
   - `mediciones.json` con los resultados de los chunks

2. **Comentar en el issue #7** con:
   - Resumen de lo aplicado (config nativa o proxy nginx)
   - Evidencia de que `content-encoding` y `cache-control` están presentes
   - Comparación de tamaños antes/después

3. **Actualizar `README.md` de `litellm-services`** si se añadió el proxy como componente nuevo del stack.

4. **Cerrar el issue #7** si C1 y C2 están resueltos, o mantenerlo abierto con el estado actual si queda trabajo pendiente.

---

## Notas importantes

- **Nunca aplicar `kubectl apply/delete/patch` como cambio permanente**: todo cambio de infraestructura va por PR en `reevolutiva-infra` y lo reconcilia Flux.
- **Solo lectura en el clúster**: `kubectl get`, `describe`, `logs`, `diff`.
- **Los manifiestos en `litellm-services` son espejo**: la fuente de verdad para despliegue es `reevolutiva-infra`. Mantener ambos alineados.
- **No imprimir bodies de endpoints administrativos de LiteLLM**: pueden contener tokens.
- **El nodo `produccion` es arm64**: verificar compatibilidad de imágenes (nginx:1.27-alpine es multi-arch).