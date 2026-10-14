# HEARTBEAT.md — checklist del ejecutor (litellm-services)

Se ejecuta **en orden** en cada wake (issue asignado o comentario que me despierta). Si un paso no
aplica, se salta; el orden no se cambia.

1. **Lee el issue completo antes de tocar nada.** Descripción, comentarios, ancestros y `blockedBy`.
   El brief vive en el issue: lo que no esté ahí, no llegó al run. Si está bloqueado por otro issue,
   no empieces: reporta el bloqueo y cierra con `blocked`.

2. **Estado real, no el reportado.** `git status -sb` y `git log --oneline -3` en este repo; para el
   servicio en alcance, `kubectl -n litellm get deploy,pods`. Compara lo que dice el issue con lo
   que hay de verdad.

3. **Plan quirúrgico.** El cambio mínimo que satisface el criterio de aceptación del issue. Si el
   issue es ambiguo, pregunta en el issue y cierra con `in_review`; no inventes alcance ni
   "aproveches" para refactorizar.

4. **Guardarraíles de la delegación.** Si dependo de otra pieza, creo un **blocker de primera clase**
   (`blockedByIssueIds`) en vez de esperar. Si necesito a otro agente, delego con un **issue hijo
   autocontenido** — nunca le pido que comente en mi issue, porque sus escrituras son
   subtree-scoped — y lo enlazo con `parentId` + `goalId`.

5. **Validación empírica antes del PR** (TDD o SDD, según el caso):
   - Servicios: una prueba que **falle antes** y pase después, o la prueba funcional del endpoint
     real (`/health/readiness`, `/readyz`, `/health`, `POST /v1/embeddings`, `/metrics`).
   - Manifiestos: validación local (`yaml.safe_load`, `kustomize build`) y `kubectl diff` contra el
     clúster.
   - Nunca declares terminado con `get` ni con `Ready=True`: las kustomizations usan `wait: false`.

6. **PR.** Rama `feat/…` o `fix/…`, cambios pequeños y alineados al estilo del repo, y cuerpo con
   **evidencia** (comando + salida, no adjetivos). Sin secretos.

7. **Verificación post-merge.** Cuando Flux reconcilie, confirma el efecto en el clúster con una
   prueba funcional del endpoint, no con `get`.

8. **Cierre.** Escribe la **disposición del issue** (`done` / `in_review` con revisor real /
   `blocked` con blocker / `in_progress` con continuación viva). La mecánica exacta está en la skill
   `paperclip`, que Paperclip monta en cada run. Si el control-plane write falla dos veces seguidas,
   no insistas: repórtalo y cierra con el mejor estado verificable.
