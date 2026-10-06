# litellm-services — monorepo LiteLLM consolidado

Repositorio único para los servicios del namespace `litellm` en el clúster K3s
de Reevolutiva. Reemplaza al repositorio histórico `clusterlm-dev` (archivado).

## Servicios

| Servicio | Namespace | Puerto | Imagen |
|---|---|---|---|
| **litellm** | `litellm` | 4000 | `ghcr.io/berriai/litellm:v1.103.0-rc.1` |
| **headroom** (guardrail) | `litellm` | 8787 | `registry.coyote-paridae.ts.net/headroom:0.34.0` |
| **vector-store** (PGVector) | `litellm` | 8000 | `registry.coyote-paridae.ts.net/vector-store:1.0.1` |
| **tracking-collector** | `litellm` | 8080 | `registry.coyote-paridae.ts.net/tracking-collector:0.1.2` |

## Estructura

```
litellm-services/
├── litellm/              # HelmRelease + Ingress del proxy LiteLLM
├── headroom/             # Guardrail de compresión (headroom-ai)
│   └── build-context/     # Dockerfile de la imagen
├── vector-store/         # Servicio de embeddings + PGVector
│   └── build-context/     # Código Python, Prisma schema, Dockerfile
├── tracking-collector/   # Colector de métricas Matomo
│   └── app/               # Código Python del collector
└── README.md
```

## Flujo de build

```bash
# Desde vm-services, dentro del directorio del servicio:
cd headroom/build-context
docker build --platform linux/amd64 --provenance=false \
  -t registry.coyote-paridae.ts.net/headroom:$(git rev-parse --short HEAD) .
docker push registry.coyote-paridae.ts.net/headroom:$(git rev-parse --short HEAD)
```

## Despliegue

Los manifiestos Kubernetes de este repo se referencian desde el repo GitOps
`reevolutiva-infra/apps/<servicio>/`. No se aplican directamente con `kubectl`.

## Historial

- **clusterlm-dev** (archivado): repositorio original con docker-compose para
  LiteLLM + headroom + vector-store en `frontdoor`.
- **2026-10-06**: consolidación en este monorepo, todos bajo namespace `litellm`.

