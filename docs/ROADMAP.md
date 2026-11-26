# 🗺️ Roadmap de openPic

Estado al cierre de esta sesión (26/11/2026). Todo lo que se habla aquí está
**pendiente** o **a medias**, con el contexto suficiente para retomarlo sin
reconstruir la conversación.

Estado del código: `main` en `git@github.com:GabrielCatDeveloper/OpenPic.git`
· 291 tests en verde · CI en verde (3.11, 3.12, 3.13).

---

## 🔴 Lo más importante: el plan de comidas

**Qué pasa hoy.** `/plan` y `/consejo` los genera Food-R1. El modelo está
afinado para el formato `<answer>...</answer>` de las calorías, no para
estructurar texto: se pasa de largo.

- Pide 2 comidas y genera **15** (la última, cortada a media por `max_tokens`).
- Pide 3-5 consejos y genera **8**, con markdown `**` que sale literal en
  Telegram (parse_mode HTML).
- Tarda **2-4 minutos** en CPU (el plan llegó a 222 s contra un
  `LLM_TIMEOUT=300`: al borde).
- El plan **se cachea entero y truncado para todo el día**.
- Está documentado como `xfail` en `tests/test_live_model.py`.

**Qué hay que hacer (por este orden).**

1. [ ] Que el plan lo generen **reglas**, no el modelo: con el perfil
   (mantenimiento, macros, margen semanal, hora) elegir platos reales de la
   tabla de ingredientes (`foodbot/data/ingredients.py`) y calcular las kcal.
   El modelo puede proponer el nombre del plato, pero los números salen de la
   tabla.
2. [ ] Recortar la respuesta del modelo a 5 viñetas y quitarle el markdown
   (`text.model_bullets` ya lo hace; falta aplicarlo al plan).
3. [ ] Bajar `max_tokens` del plan (512 → ~200) para que tarde menos.
4. [ ] Quitar el aviso `xfail` cuando el plan ya tenga el formato correcto.

**Decisión de diseño:** el modelo aporta variedad (nombres de platos), las
reglas aportan los números. Como con el consejo, que ya es determinista.

---

## 🟠 Verificar con el modelo real (nunca se ha probado)

**La lectura de etiquetas** (`foodbot/core/label.py`) usa un prompt **distinto**
del "sagrado". Food-R1 puede no obedecerlo. No se ha probado nunca contra el
modelo real: los tests son unitarios (parseo de una transcripción inventada).

- [ ] Probar `/etiqueta` con una foto de etiqueta real (yogur, queso,_AT_ bars)
      y ver qué transcribe el modelo.
- [ ] Si el modelo no cumple, cambiar de estrategia: que el usuario **escriba**
      los valores (`450 kcal/100g`) en vez de transcribirlos.
- [ ] Comprobar la **estimación de peso** desde el envase (`WEIGHT_PROMPT`).
- [ ] Verificar que la foto de la etiqueta no se cuelgue en el pipeline de
      comidas (ya se enruta aparte, pero sin probar).

**Los tests live** tardan 17 minutos en CPU. Para iterar, usar el mock
(`LLM_PROVIDER=mock`) o un subconjunto con `-k`.

---

## 🟡 Lo que falta de las peticiones de esta sesión

### Catálogo de productos — falta el alta con etiqueta al vuelo
Hecho: escribir `3 magdalenas del mercadona` busca en tus productos y en la
tabla de ~170 ingredientes; si no está, ofrece darlo de alta.

- [ ] Probar de verdad la alta: `/anotar 3 magdalenas del mercadona` → pide
      etiqueta → calcula → **confirma** → guarda el meal **y** el producto.
- [ ] La clave del producto en el alta es el nombre del plato de la comida
      (`cb_label_confirm`); si la comida se llama "Merienda mercadona", el
      producto se llama así. Normalizar.
- [ ] Decidir si los productos son **por usuario** o globales. Ahora son por
      usuario (`products.user_id`); el índice cubre los globales
      (`user_id IS NULL`), pero no hay forma de crear uno global (el admin).
- [ ] Semilla del catálogo: no hay productos precargados. La tabla de
      ingredientes cubre alimentos genéricos; los de marca, no.

### Sueño — falta el resto
Hecho: `/dormir 23:30 - 07:15` registra la noche; `/dormir` a secas muestra
la media, la regularidad y explica los picuelos nocturnos.

- [ ] Botón en el menú (ahora solo hay comando). El menú tiene sitio: falta
      "😴 Mi sueño".
- [ ] El sueño **no aparece todavía** en `/semana` ni en `/hoy`: solo en su
      propia pantalla.
- [ ] El consejo no usa el sueño: si la última noche fue corta, el tip
      debería decirlo ("dormiste 5 h: el hambre de hoy es fisiológica").
- [ ] Ajustar el umbral de irregularidad (ahora desviación media > 1,5 h).
      Conviene contrastarlo con la literatura de "social jetlag".
- [ ] Faltan los datos de calidad del sueño (¿te despertaste? ¿cuántas
      veces?) y las siestas.

### Franjas
Hecho: 7 franjas, corregibles con el botón "🕐 ¿Qué comida es?".

- [ ] La franja se guarda **por comida** ✓, pero `/anotar` y el catálogo la
      deducen de la hora sin opción de corregirla (el botón solo sale tras una
      foto). Añadir el botón también en el registro manual.

### Etiquetas y peso
- [ ] Cuando la etiqueta no trae el peso neto, el modelo estima desde el
      envase. Falta **validarlo**: ¿estima bien? ¿Qué margen de error tiene?
- [ ] Falta la opción "a peso": el usuario dice el peso y se aplica.

---

## 🟢 Pendientes de producto (de antes de esta sesión)

- [ ] `analyze_photo_nutrition()` (segunda pasada JSON) es **código muerto**:
      el bot no lo llama y con el modelo real devuelve `{}` (Food-R1 solo
      habla el formato `<answer>`). Borrarlo o reescribirlo.
- [ ] El **override de mantenimiento** (`maintenance_kcal` en la BD) no tiene
      forma de fijarse desde la UI: está en `users.py` pero no lo usa nadie.
      Debería haber un "🎯 Mi mantenimiento" para quien lo sepa.
- [ ] `profiles.daily_kcal_override` y el modo manual: `/objetivo` existe y
      funciona ✓, pero el suelo de seguridad es el único límite duro. Con el
      modo suave, el objetivo manual debería ignorarse (o avisar).
- [ ] La **"referencia metabólica"** (`sex` en la BD) está mal nombrada: es
      la referencia para los números, no el sexo de la persona. Renombrar la
      columna (con migración) evitaría confusiones.
- [ ] Faltan las **fichas de producto** (el bot podría sugerir "este tiene
      450 kcal/100 g" al detectar un envasado, sin esperar a que lo pidas).

---

## 🔧 Bugs y deuda técnica vista

| # | Qué | Dónde | Estado |
|---|---|---|---|
| 1 | f-string con escape `\u` dentro de las llaves → **SyntaxError en Python 3.11** (CI entero rojo) | `public.py` (`menos/más`) | ✅ Arreglado (57e1d56) |
| 2 | Test frágil según la hora: "hoy a la 01:00" como meal antiguo fallaba entre 00:00 y 01:00 UTC | `tests/test_db.py` | ✅ Arreglado |
| 3 | Las franjas guardadas se ignoraban al mostrar (se recalculaban desde la hora) | `text.meal_line` | ✅ Arreglado |
| 4 | Las marcas de tiempo se comparaban como texto en SQL con offsets distintos → rangos rotos | `db.py` (`to_utc_iso`) | ✅ Arreglado |
| 5 | Las ventanas temporales de la calibración y del historial usaban un día en vez del rango | `users.py` | ✅ Arreglado |
| 6 | Aviso del banco con el signo cambiado ("-10126 kcal de más") | `nutrition.py` | ✅ Arreglado |
| 7 | `Message is not modified` al pulsar un botón | `public.py` (`_safe_send` + manejador global) | ✅ Arreglado |
| 8 | `ApplicationBuilder.error_handler` no existe (hay que usar `add_error_handler`) | `public.py` | ✅ Arreglado |
| 9 | Los scripts de edición se pisaban entre sí al aplicar cambios (escapes `\u` vs caracteres reales) | — | ⚠️ Usar `edit` con caracteres reales, no `replace` con escapes |

**Deuda known:**

- `foodbot/core/advice.py` y `sleep.py` tienen las palabras en español pero
  los **nombres de las variables y los comentarios** mezclan inglés/español
  sin criterio (`tips` vs `consejos`, `saved` vs `ahorrado`). Unificar.
- `foodbot/core/label.py` tiene un `LABEL_SYSTEM` que no dice nada útil
  ("transcribe solo los valores que se ven"). Se puede afinar.
- El `feedback.py` tiene un `_EmptyLabel` en `public.py` (clase auxiliar
  Colocada en el fichero equivocado).
- Los mensajes del bot están en español **sin tildes** en el código (por
  coherencia con el resto del proyecto), pero los nuevos módulos (`advice.py`,
  `sleep.py`) sí las usan. Decidir y unificar.
- No hay `LICENSE` en el repo (es público). El modelo es Apache-2.0; el
  proyecto propio, sin decidir.

---

## 🧪 Verificaciones que faltan

- [ ] **Probar el flujo completo con el modelo real** (foto → etiqueta →
      sticker → banco semanal → /semana) con datos de verdad, no con el mock.
- [ ] Comprobar que el **sticker** se ve bien en Telegram (el set
      `openpic_estado_by_open_pic_app_bot` está subido; los `file_id` en
      `foodbot/data/stickers.json`).
- [ ] El **modo suave** de verdad: sin deficit, sin cuentas, sin stickers que
      juzguen. Solo está implementado en el cálculo del objetivo; el sticker
      sigue mandando "triste" si te pasas.
- [ ] La advice con **personas trans**: verificar el texto real que sale
      (los tips de estrógenos/testosterona).
- [ ] El bot contra el **catálogo de productos** con fotos reales de
      envasados.

---

## 📌 GitHub

- [ ] Descripción del repo (se hace desde la web o con `gh auth login`).
- [ ] `LICENSE`.
- [ ] Temas (tags) del repo: `telegram-bot`, `nutrition`, `llama.cpp`,
      `food-r1`, `calorie-tracking`.
- [ ] Un `CHANGELOG.md` (o releases) para las entregas grandes.

---

## ⚙️ Estado del entorno (para retomarlo)

- **Servicios de usuario** (sin sudo), con *linger* activado → arrancan al
  encender el PC: `foodbot` y `llama-server`.
  - `systemctl --user status foodbot llama-server`
  - `journalctl --user -u foodbot -f`
  - `scripts/install_autostart.sh` (usuario) · `--system` (con sudo).
- **Motor**: Food-R1 Q8_0 en `models/`, `llama-server` con `--parallel 1`,
  `--image-max-tokens 1024`, `-ngl 0` (esta máquina: iGPU con RAM
  compartida; el BF16 no entra).
- **Deps**: `pip install --user --break-system-packages -r requirements.txt`
  (no hay `python3-venv` en el sistema). `Pillow` está instalada a mano (para
  las etiquetas de los stickers), no está en `requirements.txt`.
- **Tests**: `python -m pytest tests/ -q` (rápido) · `-m live` contra el
  motor real (17 min) · `--ignore=tests/test_live_model.py` para lo demás.
- **Servidor de stickers**: `scripts/prepare_stickers.py` (prepara los PNG) y
  `scripts/upload_stickers.py` (los sube y guarda los `file_id`).

---

## 💡 Ideas que surgieron y no se implementaron (valoradas)

1. **Web App de Telegram** (mini app en HTML/JS dentro del chat): el mejor
   dashboard posible (la barra del acumulado con gráficos, el perfil editable,
   la semana). Requiere servirlo por HTTPS (Telegram no carga `localhost`), así
   que necesita hosting.
2. **El acumulado en la bio del bot**: updating el texto del perfil con la
   barra del deficit semanal. Es lo que hacen algunos bots y es visible sin
   abrir el chat (con un rate limit de Telegram, ~1 vez por minuto).
3. **Comparar el peso con el propio "peso esperado"**: en vez de comparar con
   el peso objetivo, comparar con la curva que habría seguido el usuario si
   hubiera hecho el déficit: "llevas 2,1 kg por debajo de tu curva".
4. **Recordatorio de peso**: preguntar "¿cómo vas?" a una hora fija y
   comparar con la media de la semana.
