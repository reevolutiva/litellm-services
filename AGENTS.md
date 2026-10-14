# AGENTS.md — litellm-services

## Alcance
Monorepo de los servicios LiteLLM, Headroom, vector-store y tracking-collector. Contiene código, contextos de build y manifiestos de servicio.

## Despliegue y GitOps
- El despliegue live sigue gobernado por `reevolutiva/reevolutiva-infra`, checkout operativo en `/data/workspace/workloads/reevolutiva-infra`.
- Los manifiestos bajo este repo son un espejo de servicio y no están conectados directamente a Flux. Mantenerlos alineados mediante PR; cambiar el origen Flux requiere una decisión y un PR separado en `reevolutiva-infra`.
- No aplicar manifiestos con `kubectl` como cambio permanente. **Prohibido:** `kubectl apply`, `kubectl delete`, `kubectl patch`, `kubectl replace`, `kubectl create`, `kubectl edit`, `kubectl scale` y cualquier operación mutante/destructiva contra el clúster. Solo está permitido uso de lectura/diagnóstico (`kubectl get`, `describe`, `logs`, `diff`) cuando sea estrictamente necesario. Toda mutación de infraestructura se hace por PR en `reevolutiva-infra` y reconcilia Flux.
- Despliegue: siempre rama → PR → revisión → merge a `main`. Flux reconcilia desde `reevolutiva-infra`; este repo no publica cambios directos al clúster. Prohibido push/merge directo a `main` y force push en ramas compartidas.
- El despliegue siempre se resuelve con PR y merge a `main`; no existen aplicaciones manuales con `kubectl` como cambio permanente.
- Guardrails OpenCode activos en este repo: `.opencode/opencode.json` (permisos globales), `.opencode/agents/litellm-services.md` (rol, alcance y no negociables del agente) y `.opencode/agents/HEARTBEAT.md` (checklist de ejecución del rol). No burlear ni desactivar esos guardrails sin aprobación explícita.
- Verificar el YAML/Kustomize local antes del PR y ejecutar los tests/build relevantes del servicio modificado.

## Seguridad
- No guardar secretos ni credenciales en Git, logs o informes. Las credenciales viven en Azure Key Vault y llegan al clúster mediante External Secrets.
- Los manifests pueden declarar nombres de Secret y banderas no sensibles; revisar todo `stringData` antes de añadirlo.
- Evitar imprimir bodies de endpoints administrativos de LiteLLM: pueden incluir tokens de API.

## Imágenes y arquitectura
El clúster tiene nodos `amd64` y `arm64`. Antes de publicar/mover una imagen, verificar la arquitectura soportada y el tag exacto en el registry. Las imágenes privadas deben construirse y publicarse desde un host con acceso al tailnet según el procedimiento documentado en `README.md`.
