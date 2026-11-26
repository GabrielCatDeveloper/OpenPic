# 📋 Tareas de openPic

Cada tarea está escrita para que pueda ejecutarla alguien (o un modelo) sin
contexto previo: qué está roto, qué hay que hacer, cómo se comprueba.

**Regla de trabajo**: una tarea cada vez, con su commit. Nada se da por bueno
hasta que los tests pasan y el CI está en verde.

Comandos habituales:

```bash
python3 -m pytest tests/ -q --ignore=tests/test_live_model.py   # suite rapida
python3 -m pytest tests/ -q -m live                             # contra el motor real (17 min)
systemctl --user restart foodbot                                # reiniciar el bot
docker run --rm -v "$PWD:/w" -w /w python:3.11-slim sh -c \
  "pip install -q -r requirements.txt && python -m pytest tests/ -q -m 'not live'"
```

---

## T-01 · El plan de comidas lo generan reglas, no el modelo

**Estado**: pendiente · **Prioridad**: alta · **Bloquea**: nada

### Contexto

Hoy `/plan` llama a `Food-R1Client.make_plan()` (`foodbot/core/foodr1.py`).
El prompt (`PLAN_PROMPT`) pide 2 comidas con formato
`- COMIDA: plato | kcal | P g | C g | G g`, pero Food-R1 está afinado para el
formato `<answer>...</answer>` de las calorías y no lo obedece:

- Genera **15 líneas** en vez de 2, y la última sale cortada por `max_tokens`
  (`LLM_MAX_TOKENS=512`).
- Tarda **2-4 minutos** en CPU (medido: 222 s contra `LLM_TIMEOUT=300`).
- El texto se cachea **entero y truncado** para todo el día
  (`cmd_plan` → `db.set_cache`).

El usuario recibe un plan inservible. El consejo ya se resolvió con el mismo
enfoque: **reglas deterministas + modelo solo para la variedad**
(`foodbot/core/advice.py`).

### Pasos

1. Crear `foodbot/core/mealplan.py` con una función pura, sin I/O:
   ```python
   def build_meal_plan(
       *, maintenance: int, target_kcal: int, protein_g: float,
       carbs_g: float, fat_g: float, slot: str, now_hour: float,
       eaten_today: int, catalog: list[dict], seed: str,
   ) -> list[dict]:
       """Devuelve 2-3 platos con nombre, kcal y macros."""
   ```
   - Elige de `foodbot/data/ingredients.py` (vía `match_ingredient`) platos
     que encajen con las macros y el momento del día (desayuno / comida /
     merienda / cena).
   - Reparte las kcal entre las comidas que faltan del día.
   - `seed` sirve para que dos días seguidos no salgan exactamente lo mismo
     (hash del usuario + fecha).
2. En `foodbot/bots/public.py::cmd_plan`, **dejar de llamar al modelo**:
   - Calcular el plan con `build_meal_plan`.
   - Si el modelo está disponible, se puede usar **solo** para nombrar el
     primer plato (opcional, con timeout corto); si no, el nombre sale de la
     tabla de ingredientes.
   - Cachear el plan con la clave que ya hay (`plan:{user}:{day}:{eaten}`).
3. Formatear el plan en `foodbot/core/text.py` con una función
   `meal_plan_message(plan)` que devuelva exactamente:
   ```
   - COMIDA: plato | 480 kcal | P 32 g | C 45 g | G 18 g
   ```
   (el formato que ya pedía el prompt original).
4. Quitar del `PLAN_PROMPT` la parte de "2 comidas" (queda obsoleto) o
   dejarlo solo para el nombre del plato.

### Criterios de aceptación

- [ ] `/plan` responde en **menos de 2 segundos** (sin GPU).
- [ ] Devuelve **exactamente 2 o 3** líneas con el formato de arriba.
- [ ] Las kcal de las líneas **suman** (aproximadamente) el objetivo diario
      que queda.
- [ ] Dos días seguidos no dan el mismo plan.
- [ ] El plan respeta el **suelo de seguridad** (nunca por debajo de
      `target.floor_kcal`).
- [ ] Los tests `tests/test_live_model.py` que documentan el fallo del plan
      pasan a verde (o se borran si lafeature deja de usar el modelo).

### Verificación

```bash
python3 -m pytest tests/ -q --ignore=tests/test_live_model.py
# y a mano, en el bot:
#   /plan  ->  debe devolver 2-3 lineas "COMIDA" en segundos
```

---

## T-02 · Limpiar el código muerto y exponer lo que está a medias

**Estado**: pendiente · **Prioridad**: media

### Contexto

- `FoodR1Client.analyze_photo_nutrition()` (`foodbot/core/foodr1.py`) no lo
  llama nadie y con el modelo real devuelve `{}` (Food-R1 solo habla el
  formato `<answer>`). Es código muerto.
- `users.maintenance_kcal` (columna de la BD) se lee en `daily_target()` como
  override del mantenimiento, pero **no hay forma de fijarlo** desde el chat.
- La columna `users.sex` guarda la **referencia metabólica** (male / female /
  other), no el sexo de la persona. El nombre confunde (y ya ha caused
  confusiones en la conversación).

### Pasos

1. Borrar `analyze_photo_nutrition()` y `NUTRITION_SYSTEM` /
   `NUTRITION_PROMPT` (y sus tests, si los hay).
2. Añadir el comando `/mantenimiento` (y botón en "Mi perfil") para:
   - ver el mantenimiento actual y cómo se ha calculado (el `breakdown` de
     `EnergyTarget`);
   - fijar un mantenimiento manual (usa `users.maintenance_kcal`);
   - quitarlo (`/mantenimiento auto`).
3. Migrar `users.sex` → `users.reference` (con `MIGRATIONS` y copia de los
   valores), y renombrar en el código y en los mensajes. Dejar el nombre
   viejo funcionando si alguien lo tenía en un `UPDATE`.

### Criterios de aceptación

- [ ] `grep -rn "analyze_photo_nutrition"` no devuelve nada.
- [ ] `/mantenimiento` muestra el desglose (BMR, PAL, pasos, entrenamiento)
      y permite fijar el valor.
- [ ] El perfil de los usuarios existentes conserva sus datos tras migrar.
- [ ] Tests nuevos para el comando.

---

## T-03 · El sueño, en todas partes

**Estado**: pendiente · **Prioridad**: media

### Contexto

`/dormir 23:30 - 07:15` registra la noche (`foodbot/core/sleep.py`,
tabla `sleep`) y `/dormir` sin argumentos muestra un resumen. Pero:

- No hay **botón** en el menú (solo comando).
- El sueño **no aparece** en `/semana` ni en `/hoy`.
- El **consejo** no lo usa: si la última noche fue corta, debería decirlo
  (la evidencia: dormir <6 h sube la ingesta >250 kcal/día).

### Pasos

1. Botón "😴 Mi sueño" en el menú persistente (`main_reply_keyboard`).
2. Añadir un bloque de sueño a `weekly_message()`: media de la semana,
   regularidad y si el último piqueo nocturno coincide con acostarse tarde.
3. En `foodbot/core/advice.py::specific_tips`, añadir un tip cuando la
   última noche fue corta (<6 h) o irregular, con el dato y la fuente
   (Nature Reviews Endocrinology 2022).
4. Guardar también la calidad del sueño (1-5, opcional) y las siestas
   (opcional).

### Criterios de aceptación

- [ ] El botón funciona y `/dormir` sigue funcionando.
- [ ] `/semana` menciona el sueño si hay noches registradas.
- [ ] El consejo menciona el sueño cuando toca.

---

## T-04 · El catálogo de productos, de punta a punta

**Estado**: pendiente · **Prioridad**: media

### Contexto

`foodbot/core/products.py` + tabla `products`. Escribir
`3 magdalenas del mercadona` busca en los productos del usuario y en la tabla
de ingredientes. Si no está, el bot pide la etiqueta y lo da de alta.

Pendiente:
- El nombre del producto al dar de alta se saca del **nombre del plato** de
  la comida ("Merienda mercadona" → el producto se llama así). Hay que
  normalizar ("3 x magdalenas" → "magdalenas").
- No hay forma de crear un producto **global** (el admin). La búsqueda ya
  los contempla (`user_id IS NULL`).
- No hay **semilla** de productos de marca.

### Pasos

1. Limpiar el nombre del producto al guardarlo (quitar cantidades y prefijos).
2. Comando `/catalogo` para listar los productos del usuario y borrar uno.
3. Permitir al admin dar de alta un producto global (con `user_id = NULL`)
   desde el bot privado.

### Criterios de aceptación

- [ ] El alta de un producto con etiqueta crea un producto con nombre limpio.
- [ ] `/catalogo` lista y borra.
- [ ] Test del flujo completo: alta → búsqueda → cálculo.

---

## T-05 · Verificar contra el modelo real (etiquetas y peso)

**Estado**: pendiente · **Prioridad**: alta · **Riesgo**: puede cambiar el
diseño

### Contexto

`foodbot/core/label.py` usa un prompt **distinto** del "sagrado"
(`LABEL_PROMPT`, `WEIGHT_PROMPT`). Food-R1 puede no obedecerlo. **Nunca se
ha probado con una foto de etiqueta real**: los tests son unitarios (parsean
una transcripción escrita a mano).

### Pasos

1. Conseguir 3 fotos de etiquetas reales (yogur, queso curado, barrita) y
   meterlas en `data/` (ignorado por git).
2. Probar `/etiqueta` con cada una y anotar qué transcribe el modelo.
3. Si la transcripción es fiable → dejarlo. Si no → cambiar el flujo: que el
   usuario **escriba** los valores (`450 kcal/100g`) en vez de transcribirlos.
4. Medir el error de la **estimación de peso** desde el envase.

### Criterios de aceptación

- [ ] Decisión documentada en `docs/ROADMAP.md` (funciona / se cambia).
- [ ] Test live (marcado `live`) que compruebe la transcripción con una foto
      real, si se decide que la vía es la transcripción.

---

## T-06 · Deuda técnica

**Estado**: pendiente · **Prioridad**: baja

- [ ] Unificar el idioma del código: hay módulos nuevos con tildes en los
      mensajes y sin tildes en los nombres de variables. Decidir una regla
      (propuesta: código sin tildes, mensajes con tildes) y aplicarla.
- [ ] Mover `_EmptyLabel` de `foodbot/bots/public.py` a
      `foodbot/core/label.py` (o InlineKeyboard equivalente).
- [ ] Afinar `LABEL_SYSTEM` (ahora es una frase que no aporta).
- [ ] Revisar `foodbot/core/advice.py` y `sleep.py`: nombres en inglés dentro
      de mensajes en español.

---

## T-07 · GitHub

**Estado**: pendiente · **Prioridad**: baja

- [ ] Descripción del repo (web o `gh auth login`).
- [ ] `LICENSE` (el modelo es Apache-2.0; el proyecto propio, por decidir).
- [ ] Tags: `telegram-bot`, `nutrition`, `llama.cpp`, `food-r1`, `calorie-tracking`.
- [ ] `CHANGELOG.md` o releases por cada entrega grande.

---

## T-08 · Verificaciones finales con el modelo real

**Estado**: pendiente · **Prioridad**: media

- [ ] Flujo completo con fotos reales: foto → sticker → banco → `/semana`.
- [ ] El sticker se ve bien en Telegram (set `openpic_estado_by_open_pic_app_bot`).
- [ ] Modo suave real: sin deficit, sin cuentas, **y sin stickers que juzguen**
      (hoy el sticker sigue mandando "triste" si te pasas).
- [ ] Consejo para personas trans: leer el texto real que sale.
