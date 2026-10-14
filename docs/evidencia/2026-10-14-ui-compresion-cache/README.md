# Evidencia — compresión y caché de estáticos de LiteLLM, 2026-10-14

Medición realizada desde `/tmp` hacia el endpoint expuesto por Tailscale `litellm.coyote-paridae.ts.net`.

## URL base
- UI: `https://litellm.coyote-paridae.ts.net/ui/`
- Assets: `https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/...`

## Chunks JS medidos (rutas con asset-prefix)
| Archivo | Tamaño bytes | content-encoding | cache-control | Notas |
|---|---|---:|---|---|
| `1kpvojzxb_2ce.js` | 31363 | (ninguno) | (ninguno) | Con `Accept-Encoding: gzip, br, zstd` no se comprime |
| `28fzwmvhc4sv1.js` | (similar) | (ninguno) | (ninguno) | Sin headers de compresión/caché |
| `1xkrmcontg-7s.js` | 30482 | (ninguno) | (ninguno) | Idem |
| `1kabwy23ggmhn.js` | 24889 | (ninguno) | (ninguno) | Idem |
| `2riseu9p5tv2u.js` | 34285 | (ninguno) | (ninguno) | Idem |
| `2dsu84-anah7m.js` | 32788 | (ninguno) | (ninguno) | Idem |
| `6l6jdje-dm0h9.js` | 5515 | (ninguno) | (ninguno) | Idem |
| `3bd8xan-w6y9j.js` | 5269 | (ninguno) | (ninguno) | Idem |
| `turbopack-2-a_44aw0u4dz.js` | ~varios KB | (ninguno) | (ninguno) | Idem |

## CSS medidos
| Archivo | Tamaño bytes | content-encoding | cache-control |
|---|---|---:|---|
| `1kid9zr1--h6y.css` | 2031 | (ninguno) | (ninguno) |
| `3146e697tym4_.css` | 196764 | (ninguno) | (ninguno) |

## HTML raíz `/ui/`
Devuelve 200 con `Content-Type: text/html; charset=utf-8`, `Content-Length: 22981`, `ETag`, `Last-Modified`, sin `content-encoding`, sin `cache-control`.

## Tiempos de ejemplo (medición de una request)
- URL: `.../1kpvojzxb_2ce.js` (31 KB)
- Con `requests.get(url, headers={'Accept-Encoding': 'gzip, br, zstd'})`:
  - `elapsed.total_seconds() ≈ 1.51 s` (tiempo hasta respuesta)
  - `total ≈ 4.66 s` (descarga completa)
  - No hay compresión aplicada (tamaño descargado == tamaño del contenido)

## Conclusión
- **C1 confirmado:** el backend de LiteLLM que sirve los assets (`/_next/static/...`) **no negocia compresión** pese a recibir `Accept-Encoding: gzip, br, zstd`. Todas las respuestas JS/CSS/HTML devueltas carecen de `content-encoding`.
- **C2 confirmado:** tampoco envía `cache-control`. Los chunks de Next.js tienen hash, por lo que son inmutables y podrían servirse con `cache-control: public, max-age=31536000, immutable` para aprovechar caché del navegador.
- Esto coincide con el hallazgo de la evidencia del 2026-10-13: 0/34 respuestas con compresión y 0/34 con cache-control.

## Comandos usados
```bash
# Ejemplo
curl -sS -D - -H 'Accept-Encoding: gzip, br, zstd' \
  'https://litellm.coyote-paridae.ts.net/litellm-asset-prefix/_next/static/chunks/1kpvojzxb_2ce.js'
```
