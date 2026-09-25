# ADR-005 — Seguridad de la API local: "local" no significa "seguro"

Estado: Aceptado (decisión del usuario, 2026-09-25) · Fecha: 2026-09-25
Aplica a: backend web de I8 (`i8-api.md` §11, RNFW-01). No afecta a la CLI.

## Contexto
El borrador v0.1 de `i8-api.md` planteaba una API sin autenticación porque escucha en `127.0.0.1` y la usa un solo usuario. Esa premisa es falsa. `127.0.0.1` es alcanzable desde **cualquier página web abierta en el navegador del usuario**, no solo desde nuestro front:

- **CSRF.** Una web cualquiera puede enviar un `POST` con `multipart/form-data` a `http://127.0.0.1:<puerto>/api/jobs` mediante un formulario o un `fetch` en modo `no-cors`. Es una "petición simple" (sin cabeceras propias), así que el navegador la envía **sin preflight**. La web atacante no puede leer la respuesta, pero no le hace falta: le basta con encolar trabajos de dos horas o llenar el disco.
- **DNS rebinding.** Una web en `ataque.example` hace que su dominio resuelva a `127.0.0.1`. Para el navegador, las peticiones siguen siendo del mismo origen, así que la política de mismo origen no protege: la web puede **leer** todo (`/api/jobs`, los stems, `/api/system` con rutas del disco) y borrar trabajos.
- **Otros procesos y usuarios del equipo.** En una máquina compartida, cualquier usuario local puede conectarse al puerto de loopback.
- Una cookie de sesión no resolvería nada: el navegador la adjunta sola en las peticiones CSRF. Además, `localhost` con otro puerto se considera el mismo sitio, así que `SameSite` no la aísla.

## Opciones
1. **Sin protección ("es local").** Vulnerable a los tres vectores. Descartada.
2. **Solo comprobar `Origin`.** Frena el CSRF de navegador, pero no el DNS rebinding con peticiones sin `Origin` (por ejemplo, un `GET` de navegación) ni a los procesos locales.
3. **Cookie de sesión.** Vulnerable a CSRF por diseño (ver arriba). Descartada.
4. **Defensa en capas: loopback + `Host` + `Origin` + token + cabecera propia.** Cada capa cubre lo que otra no cubre. Elegida.

## Decisión
1. **Solo loopback.** El servidor escucha únicamente en `127.0.0.1` (y `::1` si se habilita). Si se configura otra dirección (`0.0.0.0`, una IP de red), se niega a arrancar.
2. **`Host` en lista blanca.** Solo se aceptan `127.0.0.1:<puerto>`, `localhost:<puerto>` y `[::1]:<puerto>`. Cualquier otro valor → `403`. Esto neutraliza el DNS rebinding, porque la petición llega con `Host: ataque.example`.
3. **`Origin` en lista blanca.** Si la petición trae `Origin`, debe ser `http://` más uno de los hosts anteriores; si no → `403`. El servidor **nunca** emite cabeceras CORS (`Access-Control-Allow-*`), así que el preflight de un origen ajeno falla siempre.
4. **Token por arranque.** Al arrancar, el servidor genera un token aleatorio (`secrets.token_urlsafe(32)`), imprime en la terminal la URL de acceso `http://127.0.0.1:<puerto>/?token=<token>` y **exige el token en cada petición a `/api`**. La comparación se hace en tiempo constante (`hmac.compare_digest`). El token vive solo en memoria: un reinicio invalida el anterior. El front lo lee de la URL, lo guarda en `sessionStorage` y lo quita de la barra de direcciones con `history.replaceState`.
5. **Cabecera propia que fuerza preflight.** Las peticiones que cambian estado (`POST`, `DELETE`) deben llevar el token en la cabecera `X-StemSeparator-Token`. Una cabecera no estándar convierte cualquier petición en "no simple": el navegador hace preflight, y el preflight falla por el punto 3. Un formulario HTML no puede añadir cabeceras.
6. **Excepción acotada para lecturas en streaming.** `EventSource`, `<audio src>` y los enlaces de descarga no pueden enviar cabeceras propias. Solo para `GET /api/jobs/{id}/events`, `GET /api/jobs/{id}/stems/{stem}` y `GET /api/jobs/{id}/archive`, el token se acepta también como parámetro `?token=`. Son lecturas sin efectos: el CSRF no puede leer la respuesta y el DNS rebinding ya lo bloquea el punto 2, así que el token por query no abre una vía nueva. Las comprobaciones de `Host` y `Origin` se aplican igual. Para que el token de la URL no se filtre:
   - **`Referrer-Policy: no-referrer`** en todas las respuestas (API y front). Sin ella, cualquier navegación saliente desde una página cuya URL lleve el token lo enviaría en el `Referer`.
   - `Cache-Control: no-store` en todas las respuestas de `/api`.
   - El servidor **no registra la query string** en sus logs, solo el método y la ruta.
7. **Respuestas.** `403` con el cuerpo de error de `i8-api.md` §6 (`code: "forbidden"`) para `Host` u `Origin` inválidos; `401` (`code: "unauthorized"`) si falta el token o no es válido. El mensaje no revela el token ni cuál de las comprobaciones falló más allá de lo imprescindible.

## Alternativa evaluada para las lecturas en streaming: cookie `HttpOnly; SameSite=Strict` + `Origin`/`Host`

Sería atractiva: `EventSource`, `<audio>` y los enlaces de descarga envían cookies, y así el token no aparecería en ninguna URL. Contra el CSRF desde otro sitio (`ataque.example`) funciona: con `SameSite=Strict` la cookie no viaja en peticiones cross-site, y `Host` frena el DNS rebinding. **Se descarta por una razón técnica: las cookies no aíslan por puerto** (RFC 6265 §8.5).

- **La credencial se filtra a cualquier servicio local.** El navegador envía una cookie de `127.0.0.1` a cualquier servicio que escuche en `127.0.0.1`, en cualquier puerto. Si el usuario visita otra aplicación local (un servidor de desarrollo en `127.0.0.1:3000`, o el servicio web de otro usuario del equipo), su navegador le entrega la cookie, y ese proceso obtiene acceso completo a la API. Es precisamente el vector de "otros procesos y usuarios del equipo" que el token cierra. `HttpOnly` no lo evita, porque la fuga ocurre en el servidor que recibe la cookie, no en JavaScript. El prefijo `__Host-` exige `Secure`, y eso no está garantizado sobre `http://127.0.0.1`.
- **`SameSite` tampoco separa puertos.** "Sitio" es esquema más dominio registrable, sin puerto: una página de `localhost:3000` es del mismo sitio, así que la cookie viajaría en sus `<audio>` y en sus navegaciones, que no llevan `Origin`. Se podría compensar exigiendo `Sec-Fetch-Site: same-origin`, pero eso no arregla el punto anterior.

El token en la query, en cambio, solo lo conoce quien abrió la URL impresa. Con `Referrer-Policy: no-referrer`, sin registrar la query y con el alcance limitado a tres lecturas, su exposición residual es la lista de descargas del navegador, y el token caduca al reiniciar el servidor. Si esa exposición llegara a importar, la mejora prevista son URLs firmadas de corta duración por recurso (un *ticket* por stem o flujo, obtenido con el token en la cabecera), no una cookie.

## Consecuencias
- El usuario entra siempre por la URL que imprime el servidor (o que abre `stemseparator serve`, ver decisión abierta 3 de `i8-api.md`). Si reinicia el servidor, el front recibe `401` y debe pedirle que abra la URL nueva.
- Los tests de aceptación incluyen peticiones hostiles (CAW-13): `Host` ajeno, `Origin` ajeno, sin token, `POST` sin la cabecera propia. También comprueban `Referrer-Policy: no-referrer` y que la query no aparece en los logs.
- Un proceso local con permisos del mismo usuario puede leer la terminal o la memoria y obtener el token. Eso queda fuera del modelo de amenaza: quien ya ejecuta código como el usuario no necesita esta API.
- La API no es apta para exponerse en red. Hacerlo requeriría otro ADR (TLS, autenticación real).
