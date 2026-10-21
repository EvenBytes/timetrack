# timetrack

Prototipo para Windows que registra en qué aplicación y ventana trabajas, lee el texto de la
pantalla con el OCR nativo de Windows y clasifica el tiempo en tareas mediante reglas.
Se usa desde un icono en la bandeja del sistema y un panel web local.

## Resumen ejecutivo

**El problema.** Imputar horas a clientes y proyectos se hace de memoria al final del día o de
la semana. El resultado es impreciso, lleva tiempo y suele quedarse corto: los correos, los chats y
los cambios rápidos de contexto no llegan a apuntarse.

**La solución.** timetrack registra automáticamente en qué se trabaja y lo convierte en un parte
de horas por tarea, sin que la persona tenga que apuntar nada:

- sabe qué aplicación, ventana y web están en primer plano;
- lee el texto visible en pantalla y el asunto del correo abierto;
- unas reglas sencillas (por ejemplo, «si el título o el texto menciona X, es el cliente X»)
  asignan cada minuto a una tarea y a un tipo de actividad (correo, reuniones, chats,
  documentos, desarrollo…).

**Qué aporta.**

- Partes de horas más fiables, con el tiempo real dedicado a cada cliente o proyecto.
- Menos tiempo administrativo: el parte se revisa y se exporta (CSV) en lugar de reconstruirse.
- Visibilidad de *cómo* se trabaja: cuánto tiempo va a correo, reuniones o trabajo de fondo.
- Mejora continua: lo que queda sin clasificar se convierte en una regla nueva con un clic, y el
  cambio se aplica también a todo el histórico.

**Principios.** Funciona entero en el equipo del usuario y sin servicios externos (ver
[Privacidad y seguridad](#privacidad-y-seguridad)), no necesita instalación ni permisos de
administrador y la persona controla en todo momento qué se registra (pausa, exclusiones, reglas).

## Privacidad y seguridad

timetrack **se ejecuta solo en local y no envía datos a ningún sitio**.

- **Sin red.** No hay servidor, cuenta, nube ni telemetría. El código no hace conexiones
  salientes. El OCR usa el motor integrado de Windows (`Windows.Media.Ocr`), que trabaja sin
  conexión, y la URL de los navegadores se lee de la propia ventana con UI Automation, sin
  extensiones.
- **Panel solo local.** El panel web escucha únicamente en `127.0.0.1`, así que no es accesible
  desde otros equipos de la red. Además exige la cabecera `Host` local, lo que evita ataques de
  *DNS rebinding*, y solo acepta escrituras en JSON, lo que impide que otra web las provoque
  desde el navegador.
- **Sin capturas guardadas.** Las imágenes de pantalla se procesan en memoria y se descartan. Solo
  se guarda el texto reconocido.
- **Datos sensibles enmascarados.** Antes de guardar, el texto se limpia de emails, IBAN,
  números largos (tarjetas, cuentas) y valores tras `password:`, `token:` o `contraseña:`.
- **Exclusiones.** En `config.toml` se definen los procesos y títulos de ventana (por defecto, gestores de
  contraseñas y ventanas de navegación privada; se pueden añadir otros, como la banca) en los que nunca se hace OCR ni se lee la URL.
- **De las URL**, solo se guarda el dominio y la ruta, sin parámetros, que es donde suelen ir
  los tokens y los datos personales.
- **Datos bajo control del usuario.** Todo queda en un fichero SQLite (`data\timesheet.db`)
  dentro de la carpeta de la aplicación. Se puede consultar, exportar o borrar cuando se quiera.
  Desinstalar es borrar la carpeta.
- **Código abierto y sin privilegios.** El código de este repositorio es todo lo que se ejecuta.
  Funciona con los permisos del usuario, sin servicios de Windows ni elevación, y se puede pausar
  o cerrar desde el icono de la bandeja en cualquier momento.

## Uso diario

1. Ejecuta `timetrack.exe` (doble clic). Aparece un reloj azul en la bandeja del sistema y
   empieza a registrar; no abre ninguna ventana.
2. Clic en el icono (o *Abrir panel*) para abrir el panel en el navegador:
   `http://127.0.0.1:8765`.
3. Menú del icono (clic derecho):
   - **Abrir panel**
   - **En pausa**: deja de registrar (el icono se vuelve naranja).
   - **Iniciar con Windows**: arranque automático al iniciar sesión.
   - **Salir**

Si ya está en marcha y lo vuelves a abrir, no arranca una segunda copia: abre el panel.

### Panel

- **Día**: tiempo trabajado, línea temporal por tarea, por tipo de aplicación y por
  aplicación, tiempo por tarea (con su reparto por tipo), tiempo por tipo de aplicación (con su
  reparto por tarea) y lista de actividades. Pulsa una actividad para ver el texto que reconoció el OCR y, si
  quieres, crear una regla a partir de ella.
- **Semana**: tabla por día con totales, por tarea o por tipo de aplicación.
- **Sin clasificar**: lo que más tiempo ocupa sin regla (día, 7 o 30 días), con un botón
  *Crear regla* que rellena el título (o el tema, en los correos).
- **Reglas**: de tareas o de tipos de aplicación (selector arriba). Crear, editar, reordenar (↑ ↓; gana la primera que coincida) y eliminar reglas.
  *Vista previa* muestra cuánto tiempo de los últimos 7 días capturaría la regla y qué tarea
  tiene ahora ese tiempo. *Guardar* valida las expresiones regulares, escribe `rules.toml` y
  se aplica al momento al registro y a todo el histórico.
- **Exportar CSV** del día o de la semana mostrada.

## Cómo funciona

- Cada **1 s** mira la ventana activa (proceso + título) y la inactividad de teclado/ratón.
  Un cambio de ventana o de título abre una actividad nueva; 5 min sin entrada o la sesión
  bloqueada cuentan como *ausente*.
- Como mucho cada **15 s** captura la ventana activa y, si la imagen cambió, extrae su texto
  con `Windows.Media.Ocr` (local, sin red). El primer OCR se hace 2 s después de cambiar de
  ventana. Solo se guarda el texto, enmascarado (emails, IBAN, números largos, contraseñas);
  las imágenes no se guardan nunca.
- En los navegadores (Chrome, Edge, Brave, Vivaldi, Opera, Firefox) lee la **URL** de la
  barra de direcciones con UI Automation, sin extensiones. Solo guarda dominio + ruta (sin
  parámetros), un cambio de dominio abre actividad nueva, el panel muestra el dominio como
  aplicación y las reglas pueden usar el campo `url`.
- En las aplicaciones de `[[topic.apps]]` (de momento Outlook y Gmail, reconocidas por URL o
  título) se saca además el **tema**: la línea de letra más grande del panel de lectura, que
  es el asunto del correo abierto. Abrir otro correo parte la actividad, y las reglas pueden
  usar el campo `topic`.
- Todo va a `data\timesheet.db` (SQLite) en bruto. La clasificación se calcula al consultar,
  así que al cambiar una regla se reclasifica también el histórico.
- El panel solo escucha en `127.0.0.1` y no es accesible desde otros equipos.

## Ejecutables

```powershell
powershell -ExecutionPolicy Bypass -File .\build_exe.ps1
```

Genera en `dist\timetrack`:

| Fichero | Para qué |
|---|---|
| `timetrack.exe` | App de bandeja, sin consola (doble clic, inicio con Windows) |
| `timetrack-cli.exe` | Los mismos comandos con consola |
| `config.toml`, `rules.toml`, `categories.toml` | Se copian solo si no existen, para no pisar tu configuración |
| `data\` | Base de datos y `timetrack.log` (se crean al usarlo) |

La carpeta es autónoma: no necesita Python y se puede mover donde quieras.

### Comandos (`timetrack-cli.exe` o `python -m timetrack`)

```
app            icono en la bandeja + registro + panel (por defecto)
panel          solo el panel web, sin registrar
track          registrar en la consola (Ctrl+C para parar)
report         resumen por tarea      [--date YYYY-MM-DD] [--away]
timeline       bloques del día        [--date YYYY-MM-DD]
unclassified   lo que falta por clasificar
export         CSV                    [--date YYYY-MM-DD] [--out fichero.csv]
ocr-test       OCR de la ventana activa [--delay 3]
```

## Desarrollo

Requiere Python 3.11 o superior en Windows 10/11 y al menos un idioma con OCR instalado
(Configuración > Hora e idioma > Idioma > *idioma* > Opciones > Reconocimiento óptico de caracteres).

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item rules.example.toml rules.toml   # tus reglas; rules.toml no se sube al repositorio
python -m timetrack app
```

## Configuración

- `config.toml`: intervalos, umbral de ausencia, idioma OCR, privacidad (procesos y títulos
  en los que nunca se hace OCR), puerto del panel y aplicaciones con tema (`[topic]`).
- `rules.toml`: reglas de clasificación en tareas (editables desde el panel o a mano).
- `categories.toml`: tipos de aplicación (IA, Correo, Reuniones, Chats, Documentos,
  Desarrollo, Archivos, Web; lo demás es «Otros»). Mismo formato que `rules.toml`; es una
  clasificación paralela: dice *con qué* trabajas, las tareas dicen *para quién*.
