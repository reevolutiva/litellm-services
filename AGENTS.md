# AGENTS.md — litellm-services

## Alcance
Monorepo de los servicios LiteLLM, Headroom, vector-store y tracking-collector. Contiene código, contextos de build y manifiestos de servicio.

## Despliegue y GitOps
- El despliegue live sigue gobernado por `reevolutiva/reevolutiva-infra`, checkout operativo en `/data/workspace/workloads/reevolutiva-infra`.
- Los manifiestos bajo este repo son un espejo de servicio y no están conectados directamente a Flux. Mantenerlos alineados mediante PR; cambiar el origen Flux requiere una decisión y un PR separado en `reevolutiva-infra`.
- No aplicar manifiestos con `kubectl` como cambio permanente. Seguir rama → PR → revisión → merge a `main`; no hacer push/merge directo a `main`.
- Verificar el YAML/Kustomize local antes del PR y ejecutar los tests/build relevantes del servicio modificado.

## Seguridad
- No guardar secretos ni credenciales en Git, logs o informes. Las credenciales viven en Azure Key Vault y llegan al clúster mediante External Secrets.
- Los manifests pueden declarar nombres de Secret y banderas no sensibles; revisar todo `stringData` antes de añadirlo.
- Evitar imprimir bodies de endpoints administrativos de LiteLLM: pueden incluir tokens de API.

## Imágenes y arquitectura
El clúster tiene nodos `amd64` y `arm64`. Antes de publicar/mover una imagen, verificar la arquitectura soportada y el tag exacto en el registry. Las imágenes privadas deben construirse y publicarse desde un host con acceso al tailnet según el procedimiento documentado en `README.md`.
