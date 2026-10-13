# Evidencia — rendimiento de la UI de LiteLLM, 2026-10-13

Captura de red de la UI de LiteLLM tomada **desde el dispositivo del dueño**, usada
para separar lo que es responsabilidad de este repo de lo que no lo es.

## Procedencia

| | |
|---|---|
| Fecha (UTC) | 2026-10-13 19:29:57 → 19:34:30 |
| Página | `https://litellm.coyote-paridae.ts.net/ui/logs/` y `/ui/` |
| Cliente | Pixel 10 · Android 16 · Chrome 155 (WebInspector 537.36) |
| Instrumento | DevTools → Network → *Preserve log* + *Disable cache* + `Ctrl+Shift+R` |
| Salida | HAR completo (no versionado: 1,4 MB) + `lecturas.csv` (esta carpeta) |

**Saneamiento:** se revisó el HAR header por header en busca de
`authorization|cookie|set-cookie|api[-_]?key|token|secret|password|bearer`.
Resultado: **0 coincidencias**. Los valores de `Cookie`, `Set-Cookie` y
`Authorization` no están en la captura, así que no hay nada que redactar. Aun así solo
se versiona la tabla derivada — no los cuerpos de las respuestas ni el HAR completo.

## `lecturas.csv`

Una fila por request (34). Columnas: número, método, URL (sin esquema), status,
versión HTTP, tipo de recurso, **TTFB** (`timings.wait`), duración total, bytes de
cuerpo, bytes por el cable, `content-encoding` de la respuesta, `cache-control` de
respuesta y de request, IP del servidor, id de conexión y hora de inicio.

## Lo que muestra

| Medida | Valor |
|---|---|
| Requests | 34 (33 con `200`, la #0 es un `ERR_ABORTED` al inicio) |
| Conexión | **una sola**, HTTP/2, id `695397`, contra `100.118.154.36` |
| Cuerpo total | **1 287 KB** — de los cuales **949 KB son JS** |
| Respuestas con `content-encoding` | **0 de 34** |
| Respuestas con `cache-control` | **0 de 34** |
| TTFB | mediana **82,2 s**, máximo **138,3 s** |
| Velocidad (cuerpos >4 KB, n=30) | min 0,04 · mediana **0,16** · máx 5,97 KB/s |

## Los dos defectos que sí son de acá

**C1 — Los estáticos viajan sin comprimir.** 0 de 34 respuestas traen
`content-encoding`, con `accept-encoding: gzip, deflate, br, zstd` presente en todos
los requests. Los 949 KB de JS serían ~285 KB con gzip: **3,3× menos bytes para el
mismo contenido**, y es el arreglo más barato de todos.

**C2 — Nada se cachea.** Ni una respuesta trae `cache-control`. Los chunks de Next.js
llevan hash en el nombre, así que son inmutables por construcción y podrían servirse
con caché larga sin riesgo. *Salvedad honesta:* los 34 requests traen
`cache-control: no-cache` **de parte del cliente**, que es el artefacto esperable de
tener *Disable cache* activado en DevTools — el único modo de capturar esto de forma
reproducible. Es decir: la ausencia de cabecera en la **respuesta** es el hallazgo; el
efecto en el cliente está alterado por el instrumento y conviene reconfirmarlo con una
segunda captura sin *Disable cache*.

## Lo que **no** se arregla acá

El HAR no puede separar relay DERP de último tramo del dispositivo, y tampoco es su
trabajo: la causa dominante de la lentitud **no está en estos servicios**.

Está medido y documentado en `reevolutiva/reevolutiva-infra`,
`docs/topologia-y-rendimiento-tailnet.md`: el pod de LiteLLM y su proxy del tailnet
corren en el nodo de `produccion`, que hoy es **un solo nodo en Azure westus3
(Arizona)**, mientras los usuarios están en Santiago de Chile. Medido desde Chile:
`connect` 146 ms, TLS **1 349 ms**, TTFB **1,49 s** — contra 1,9 ms / 414 ms / 0,42 s
del mismo tráfico servido por un nodo local. A eso se suma que el camino del tailnet
va por **relay DERP** y que el MTU entre nodos está desalineado (1420 B contra 1200 B).

Traducido: C1 y C2 multiplican el costo, pero la geografía y el transporte son el
costo. Arreglar acá sirve aunque se mueva el nodo — y sirve *antes*, porque reduce a
un tercio los bytes de cada viaje.
