# Plan para mejorar el rendimiento de la UI de LiteLLM

1. **Recolectar evidencia nueva de los assets estáticos**
   - Usar `curl -I`/`curl -w` apuntando a un chunk real de `/_next/static/chunks/` para capturar `content-encoding`, `cache-control`, `content-length` y tiempos (`time_starttransfer`, `size_download`).
   - Repetir con y sin `Accept-Encoding`/`--compressed` para confirmar que el backend no aplica compresión.
   - Registrar la URL exacta y los valores para adjuntarlos al issue #7.

2. **Determinar cómo se sirven los estáticos dentro del pod**
   - Revisar el HelmRelease (y el chart `litellm-helm`) para entender si Next.js corre integrado y cuáles son las rutas (`litellm-asset-prefix`, `ui`).
   - Inspeccionar la imagen `ghcr.io/berriai/litellm:v1.105.1` (versión objetivo del upgrade) o su documentación para ver si ya existe configuración de middleware (como `GZipMiddleware`, `StaticFiles`, `cache-control`).
   - Evaluar si alguna capa del entorno (Ingress tailscale, guardrail headroom, proxies) puede generar los headers faltantes.

3. **Diseñar el arreglo de compresión y caché**
   - Si la app puede habilitar compresión internamente, definir la bandera/env var necesaria y cómo inyectarla (por ejemplo, extendiendo `litellm-flags` o un ConfigMap usado por el chart).
   - Si no se puede desde dentro, diseñar un proxy intermedio (nginx/traefik) que añada los `content-encoding`/`cache-control` adecuados y mantenga el Ingress apuntando al Service `litellm:4000`.
   - Establecer qué headers deben aplicarse a los recursos con hash: `cache-control: public, max-age=31536000, immutable` y compresión gzip/BR cuando el cliente lo solicita.

4. **Verificación y documentación**
   - Después de aplicar los cambios (en `reevolutiva-infra` y espejo aquí), repetir `curl -sI <chunk>` para comprobar que `content-encoding` y `cache-control` están presentes.
   - Hacer una segunda captura en DevTools con "Disable cache" desactivado para verificar la reutilización de caché.
   - Documentar los comandos, URLs y resultados en el issue #7 y en cualquier evidencia de rendimiento necesaria.

5. **Bloqueos y coordinación**
   - Confirmar que la imagen `ghcr.io/berriai/litellm:v1.105.1` ya incluye gzip en respuestas buffereadas (commit `eb10333` en v1.105.0).
   - Coordinar los cambios de configuración con `reevolutiva/reevolutiva-infra`, ya que Flux despliega desde allí y todos los cambios deben reconciliarse por PR.
   - Validar los efectos desde un cliente real en el tailnet (ej.: Santiago/Chile) para reflejar la experiencia del usuario.
