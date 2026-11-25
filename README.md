# 🥗 openPic — Bot de nutrición con Food-R1

Bot de Telegram que analiza **fotos de comida** con el modelo **Food-R1** (Qwen3-VL-8B),
calcula calorías y macros, guarda el historial del usuario y le ayuda a cumplir
su objetivo de peso con planes de comidas, consejos y seguimiento de tendencia.

Acceso restringido: **solo usuarios con un token válido** pueden usar el bot.

---

## 🏗️ Arquitectura: dos bots, un mismo núcleo

```
┌──────────────────────┐        ┌──────────────────────────────┐
│  🤖 Bot privado      │        │  🥗 Bot público              │
│  (gate bot)          │        │  (el que usa el usuario)      │
│                      │        │                              │
│  /token  → genera   │──┐  ┌─▶│  /token → valida y activa    │
│  /autorizar → alta  │  │  │  │  📸 foto → Food-R1 → kcal    │
│  directa            │  │  │  │  📊 /hoy → macros del día    │
│  /tokens → lista     │  │  │  │  🔐 /privacidad → borra todo │
│  /revocar → anula    │  ▼  ▼  │  ✏️ /corregir → ajusta kcal  │
│  /usuarios, /stats   │  SQLite (foodbot.db)                 │
└──────────────────────┘        └──────────────────────────────┘
                                        │
                                        ▼
                               llama-server (Food-R1 GGUF)
                               endpoint OpenAI-compatible
```

El bot privado (el que genera tokens) es **de un solo usuario**: con
`SINGLE_USER_MODE=true` (por defecto) solo responde al `ADMIN_TELEGRAM_IDS`
configurado; cualquier otra persona recibe un «este es un bot de un solo
usuario» y no puede emitir ningún token.

El bot público **sí es multiusuario**: cualquiera con un token válido entra.
Cada usuario ve únicamente sus propios datos.

Los tokens se guardan **solo como hash SHA-256**: el código se muestra una única
vez al crearlo y no se puede recuperar después (por diseño).

---

## 🧠 El motor: Food-R1

| Dato | Valor |
|---|---|
| Modelo base | Qwen3-VL-8B afinado con CoT + GRPO |
| Pesos originales | [`zy12123/Food-R1`](https://huggingface.co/zy12123/Food-R1) (Apache-2.0) |
| GGUF (comunitario) | [`AKMESSI/Food-R1-GGUF`](https://huggingface.co/AKMESSI/Food-R1-GGUF) |
| MAE en calorías | **42.56 kcal** (CalorieBench-80K, R²=0.81) |
| Referencia | Gemini-2.5-flash: 301.87 · GPT-4o-mini: 88.40 · **Food-R1: 42.56** |

### ⚠️ El prompt es sacredo

Food-R1 fue entrenado con un esquema de salida **muy concreto**:

```
<answer>The dish is {DISH}, and has {COUNT} ingredients in total. They are:
{INGREDIENTS}. They have a total of {KCAL} kcal.</answer>
```

Si cambias el prompt, el modelo se desvía y deja de ser parseable.
El prompt exacto está en `foodbot/core/foodr1.py` (`CALORIE_SYSTEM` / `CALORIE_PROMPT`),
copiado del script de evaluación oficial del repo.

### 🔢 Macros: por qué no se le pregunta al modelo

Food-R1 predice **calorías** (su punto fuerte, error de 42 kcal). Los macros se calculan
de forma determinista con la tabla de ingredientes de `foodbot/data/ingredients.py`
(≈170 alimentos con kcal y macros por 100 g) y después se **escalan** para que
sumen exactamente las calorías mostradas. Así el usuario nunca ve
"620 kcal" junto a macros que suman 540.

### ⏱️ Cola de inferencia

El servidor local va con `--parallel 1`: el cliente del bot serializa las
peticiones (`LLM_CONCURRENCY=1`) para no saturar la RAM ni encolar trabajo
dentro de llama.cpp. Si llegan varias fotos a la vez, se procesan **una a una**
en lugar de pelearse por la memoria.

El plan de comidas y el consejo del día se **cachean** por usuario y día: si
vuelves a pedirlos no se vuelve a gastar GPU en la misma pregunta.

### 📐 Cuantizaciones y qué cabe en tu máquina

| Cuant | Tamaño | RAM/VRAM recomendada | Notas |
|---|---|---|---|
| `bf16` | 16.4 GB | **24 GB RAM o GPU de 16 GB VRAM** | El original, máxima fidelidad |
| `q8_0` | 8.7 GB | 12 GB | Prácticamente sin pérdida frente a BF16 |
| `q6_k` | 6.7 GB | 10 GB | Mejor equilibrio para CPU/iGPU Justa |
| `q5_k_m` | 5.9 GB | 8 GB | |
| `q4_k_m` | 5.0 GB | 6 GB | Experimental, no recomendado para nutrición |

Además hace falta el **proyector de visión** (`mmproj-Food-R1-F16.gguf`, 1.16 GB):
sin él el modelo no ve las imágenes.

#### ⚠️ Lo que se midió en esta máquina (GMKtec, 17,4 GiB RAM, Radeon 760M iGPU)

- **`bf16` NO arranca aquí.** El kernel ve 17,4 GiB (24 GB físicos menos el framebuffer
  reservado por la iGPU). Modelo 15,3 GiB + proyector 1,1 GiB + buffers ≈ 17,1 GiB:
  no entra, y el OOM killer mata el servidor durante la carga. Con `-ngl 99` el
  intento de reservar el modelo entero en la UMA falla antes con
  `device lost on Vulkan0`.
- **`q8_0` sí funciona**: 8,7 + 1,1 = 9,8 GiB, deja ~7 GiB al sistema.
- La iGPU reporta 15 GB de "VRAM" (es RAM compartida, UMA). aun así, con el modelo
  completo no hay espacio para los buffers.

**Recomendación:** `q8_0` en local, y si quieres el BF16 de verdad, una GPU
rentada de 16 GB (RunPod/Vast.ai) donde `LLM_BASE_URL` apunte al servidor remoto.

#### Resolución de la imagen (importante)

Qwen3-VL usa resolución dinámica: cada token cubre 32×32 px. Una foto de
Telegram de 4000×3000 se comería **15.000 tokens** de contexto. Por eso el
servidor arranca con `--image-max-tokens 1024`, que equivale exactamente a los
`max_pixels=1048576` del script de evaluación oficial
(1024 tokens × 32 × 32 = 1.048.576 px).

---

## 🔐 Privacidad y datos

Tres garantías, verificadas con tests:

1. **Las fotos no se quedan en tu disco.** La imagen se guarda en
   `PHOTOS_DIR` mientras se analiza y **se borra en cuanto se envía el
   resultado**. `STORE_PHOTOS=true` es el valor por defecto. Si el bot se
   cayera a mitad del análisis, el fichero se recoge por `PHOTO_TTL_HOURS`
   (1 h), al arrancar el bot y con una tarea periódica cada 30 min.
   Con `STORE_PHOTOS=false` la foto vive solo en memoria.
2. **Cada usuario solo ve lo suyo.** Todas las consultas van filtradas por
   `user_id`; borrar, corregir o exportar una comida de otro usuario no es
   posible (ni por id de meal, ni por token). Un token ya canjeado por alguien
   no puede "rescatarse" desde otra cuenta.
3. **Borrado real.** `/privacidad` muestra qué hay guardado y `/olvidarme`
   (o el botón 🗑) elimina comidas, pesos, objetivos, uso y respuestas
   cacheadas, y revoca el acceso del usuario.

El bot no guarda conversaciones, ni fotos, ni datos de terceros.

### 📸 El pipeline de una foto

```
   Telegram
      │
      │  1. RECIBIR
      ▼
   bytes en RAM  ─────────────────┐
      │                           │
      │  2. GUARDAR EN DISCO       │  data/photos/<user_id>_<ts>.jpg
      ▼                           │
   data/photos/<id>_<ts>.jpg ─────┘
      │
      │  3. EL MODELO (Food-R1)  ──▶  kcal + macros + ingredientes
      │
      │  4. ENVIAR EL RESULTADO  ──▶  mensaje en Telegram + fila en la BD
      │
      ▼
   5. ELIMINAR LA FOTO  ─────────▶  el fichero desaparece del disco
```

La eliminación va en un `finally`: la foto se borra **siempre**, incluso si el
modelo falla (en Telegram sigue estando, por eso existe el botón 🔄
Reanalizar).

---

## 🚀 Instalación

### 1. Dependencias

```bash
cd /home/gabriel/Proyectos/GabrielCatDev/openPic

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

> En Debian/Ubuntu si `venv` falla: `sudo apt install python3-venv`.
> Si tu Python es "externamente gestionado" (PEP 668): `pip install --user --break-system-packages -r requirements.txt`.

### 2. Configuración

```bash
cp .env.example .env
$EDITOR .env
```

Necesitas en `.env`:

| Variable | Qué es |
|---|---|
| `PUBLIC_BOT_TOKEN` | Token del bot **público** (lo da @BotFather) |
| `GATE_BOT_TOKEN` | Token del bot **privado** (otro bot distinto) |
| `ADMIN_TELEGRAM_IDS` | Tu id numérico de Telegram (manda `/start` a @userinfobot) |
| `SINGLE_USER_MODE` | `true` = el panel de tokens solo lo usas tú |
| `PUBLIC_BOT_USERNAME` | @usuario del bot público (para el botón «Entrar» con deep link) |
| `TIMEZONE` | Zona IANA para decidir cuándo empieza «hoy» (ej. `Europe/Madrid`) |
| `STORE_PHOTOS` | `false` = las fotos nunca tocan el disco |
| `LLM_CONCURRENCY` | Peticiones simultáneas al motor (1 en local) |

Comprueba que todo está bien (incluye un ping al motor de inferencia):

```bash
python -m foodbot.main --check
```

O un diagnóstico completo (config, pesos, motor, memoria, bots de Telegram):

```bash
python scripts/doctor.py
```

### 3. Motor de inferencia

**Opción A — local (gratis, sin GPU dedicada)**

```bash
# Compilar llama.cpp con Vulkan (AMD/Intel) o CUDA (NVIDIA)
scripts/setup_llama.sh vulkan      # o: scripts/setup_llama.sh cuda

# Descargar los pesos (elige cuantizacion segun tu hardware)
python scripts/download_model.py --quant q8_0

# Arrancar el servidor
NGL=0 scripts/run_llama_server.sh q8_0
```

> `setup_llama.sh` resuelve solo la dependencia `SPIRV-Headers` que necesita el
> backend Vulkan: si no está instalada en el sistema, extrae el paquete de Debian
> en `~/llama.cpp/deps/` **sin usar sudo**.

> `NGL=0` = ejecutar en CPU. En una iGPU con RAM compartida el reparto GPU/CPU
> suele ser contraproducente; prueba `NGL=99` y quédate con lo más rápido
> (mide con `scripts/test_inference.py`).

**Opción B — GPU remota (16 GB, mejor calidad)**

En un servidor con RTX 4060 Ti 16GB / 3090 / 4090:

```bash
python scripts/download_model.py --quant bf16
scripts/run_llama_server.sh bf16
```

Y en el `.env` del bot: `LLM_BASE_URL=http://<ip-del-servidor>:8080/v1`.

**Opción C — probar el bot sin modelo** (mock)

```bash
LLM_PROVIDER=mock python -m foodbot.main
```

El mock devuelve respuestas con el formato exacto de Food-R1, así que puedes
probar todo el flujo (tokens, fotos, historial) sin descargar 16 GB.

### 4. Arrancar

```bash
python -m foodbot.main
```

### 5. Arranque automático (al iniciar el PC)

Instalar los bots y el motor de inferencia como **servicios de usuario**, sin
`sudo`, para que arranquen solos al encender el equipo:

```bash
scripts/install_autostart.sh
```

El script hace tres cosas:

1. Copia `deploy/llama-server.user.service` y `deploy/foodbot.user.service` a
   `~/.config/systemd/user/` y los habilita (`systemctl --user enable`).
2. Activa el *linger* (`loginctl enable-linger`), que es lo que hace que el
   gestor de servicios de usuario arranque **al iniciar el PC, sin que nadie
   tenga que entrar en sesión**.
3. Arranca ambos servicios.

A partir de ahí:

```bash
systemctl --user status foodbot llama-server   # ver el estado
systemctl --user restart foodbot               # reiniciar los bots
journalctl --user -u foodbot -f                # ver el log en vivo
```

Detalles importantes:

- El servicio del bot **espera al motor** antes de atender mensajes
  (`scripts/wait_for_engine.sh`): al encender el PC el modelo tarda en
  cargarse y, sin esa espera, las primeras fotos fallarían.
- `llama-server.user.service` arranca con `NGL=0` (todo en CPU), que es lo que
  funciona en esta máquina con la iGPU y la RAM compartida. En un equipo con
  GPU dedicada, sube `NGL` a `99`.
- Los servicios se reinician solos si se caen (`Restart=always`).
- Los logs van a `logs/llama-server.log` y `logs/bot.log`.

> **Alternativa con `sudo`** (servicios del sistema, para todos los usuarios):
> `sudo cp deploy/llama-server.service deploy/foodbot.service /etc/systemd/system/`
> y `sudo systemctl enable --now llama-server foodbot`.

---

## 💬 Uso

### En el bot privado (tú)

Dos formas de dar acceso a alguien:

| Comando | Qué hace |
|---|---|
| `/token [plan] [dias] [etiqueta]` | Genera un token. Plan: `free`, `pro`, `unlimited`. Días: `0` = sin caducidad |
| `/nick <apodo> [plan] [dias]` | Igual, pero con apodo para reconocerlo (`/nick laura pro 30`) |
| `/autorizar <id\|apodo> [plan]` | **Alta directa**: activa a alguien sin token |
| `/alias <apodo> <id>` | Registra un apodo para un id (luego `/autorizar laura`) |
| `/apodos` | Lista los apodos |
| `/desactivar <id\|apodo>` | Quita el acceso (conserva el historial) |
| `/tokens` | Lista los tokens (solo muestra el prefijo, nunca el código) |
| `/revocar <id\|código>` | Invalida un token |
| `/usuarios` | Lista los usuarios autorizados (con su apodo y estado) |
| `/plan <id\|apodo> <plan>` | Cambia el plan de un usuario |
| `/stats` | Estadísticas de uso |
| `/broadcast <texto>` | Mensaje a todos los usuarios autorizados |

Ejemplo: `/token pro 30 gabriel` → genera un token `NTR1-XXXX-XXXX-XXXX` válido
30 días para el plan Pro.
Ejemplo: `/autorizar 123456789` → activa la cuenta de esa id directamente.

Si configuras `PUBLIC_BOT_USERNAME`, cada token sale con un botón **📲 Entrar
con este token** (`t.me/<bot>?start=NTR1-…`): el usuario pulsa y queda dentro
sin copiar nada.

### La interfaz: todo son botones

No hace falta escribir ningún comando. El bot pone un **teclado fijo bajo el
campo de texto** (📸 Analizar · 📊 Hoy · 🗓 Semana · ⚖️ Peso · ⚙️ Perfil) y
además botones contextuales dentro de cada mensaje:

```
┌──────────────────┬──────────────────┐
│ 📸 Analizar plato │ 📊 Mi día         │
├──────────────────┼──────────────────┤
│ 🗓 Mi semana      │ 🧭 Plan          │
├──────────────────┼──────────────────┤
│ 💡 Consejo        │ 🗂 Historial     │
├──────────────────┼──────────────────┤
│ ⚖️ Mi peso        │ 🎯 Mi meta       │
├──────────────────┼──────────────────┤
│ 🍽 Registrar      │ ⚙️ Mi perfil     │
├──────────────────┼──────────────────┤
│ 🔐 Mis datos      │ 💾 Exportar CSV  │
└──────────────────┴──────────────────┘
```

Detalles de UX que usa Telegram:

- **Force reply** para pedir datos (peso, altura, % de grasa): Telegram
  enfoca el campo de texto para que escribas sin tocar nada más.
- **Indicador de «escribiendo…»** mientras el modelo piensa (el plan puede
  tardar minutos).
- **Toasts** al confirmar (`query.answer("Guardado")`) en vez de mensajes de
  ruido.
- El asistente de perfil va **preguntando** y guarda cada respuesta, con
  botones para lo que tiene pocas opciones y texto libre para lo demás. Los
  datos opcionales se pueden saltar.

Los comandos siguen existiendo (para quien los prefiera): están en el menú de
Telegram y en `set_my_commands`.

### Comandos (también hay botones para todo)

| Comando | Qué hace |
|---|---|
| `/start` | Bienvenida / activación con token |
| `/token NTR1-...` | Canjea el token y activa la cuenta |
| `/perfil` | Asistente guiado: nombre → sexo → edad → altura → peso → % grasa → entrenamiento → pasos → trabajo → actividad → objetivo → peso objetivo |
| 📸 **foto** | Analiza el plato: calorías, macros, ingredientes, confianza |
| `/hoy` | Resumen del día: macros, lo que has ahorrado hoy y el estado del banco semanal |
| `/semana` | El banco de ahorro: presupuesto, lo ahorrado, lo que falta y el margen por día |
| `/objetivo [kcal]` | Fija tus propias calorías: el bot te dice qué significa en kg/semana |
| `/historial [días]` | Últimas comidas y media calórica (botones 7/30/90 días) |
| `/peso [kg]` | Registra peso y muestra la tendencia (kg/semana) |
| `/meta` | Progreso hacia el objetivo + ETA en semanas |
| `/plan` | Plan de comidas para el resto del día, ajustado a tu margen semanal (plan Pro) |
| `/consejo` | Consejo nutricional del día (plan Pro) |
| `/anotar plato \| kcal` | Registro manual |
| `/corregir [kcal]` | Corrige las calorías del último plato |
| `/borrar` | Borra la última comida |
| `/privacidad` | Qué datos se guardan + botón de borrado |
| `/olvidarme` | Borra todos mis datos y revoca el acceso |
| `/exportar` | Descarga el historial en CSV (plan Pro) |

> **Truco para más precisión:** al enviar la foto, escribe en el pie de foto
> lo que sepas (`150 g de pechuga, con arroz`). El modelo lo usa como contexto.

### ✏️ Corregir al modelo

El modelo se equivoca (o no ve el aceite). Después de cada análisis hay
botones:

- **✏️ Corregir** → escribes las calorías reales y se actualiza tu historial.
- **🔄 Reanalizar** → se vuelve a descargar la foto de Telegram y se repite el
  análisis (útil si el motor estaba saturado).
- **🗑 Borrar** → elimina el plato.

---

## ⚖️ Lógica nutricional: mantenimiento + banco de ahorro

Implementada en `foodbot/core/nutrition.py`.

### El modelo

El **mantenimiento** (las kcal que el cuerpo gasta al día) es la referencia.
El **objetivo diario** es el mantenimiento menos el déficit planificado, y el
déficit se mide **por semana** (1 kg de grasa ≈ 7.000 kcal):

```
Mantenimiento diario  M  =  2 174 kcal
Objetivo semanal      G  =  0.5 kg/semana × 7 000 kcal/kg = 3 500 kcal
Objetivo diario       M − G/7 = 2 174 − 500 = 1 674 kcal/día
```

Cada día que comes por debajo del mantenimiento **ahorras**; cada día que te
pasas **gastas** del banco. El banco se reparte entre los días que quedan:

```
Hoy has comido 2 400 kcal →  −226 kcal (deuda)
Faltan 4 078 kcal para el objetivo de la semana, en 4 días → 1 020 kcal/día
```

Por eso **un día fuera de la raya no rompe la semana**: los días siguientes
absorben la diferencia. Lo único que no se toca es el suelo de seguridad.

### De dónde sale el mantenimiento

| Fuente | Qué usa | Precisión |
|---|---|---|
| **Mifflin-St Jeor** (por defecto) | sexo, edad, altura, peso | ±10% en el 82% de adultos (Frankenfield 2005) |
| **Katch-McArdle** (si hay % de grasa) | masa libre = peso × (1 − %grasa) | Mejor en gente musculosa; solo con una medida fiable (DEXA o pliegues) |
| **Calibración con tus datos** | tu ingesta real + tu peso real | La más precisa: sustituye a la fórmula |

La calibración es la parte interesante: si promedias 2 000 kcal/día y bajas
0,5 kg/semana, tu mantenimiento **no** es el que dice la fórmula, son
2 000 + 550 = 2 550 kcal. El bot lo calcula solo cuando tiene al menos 14 días
de registros y dos pesos separados por 10 días.

### Qué datos pide el bot

El asistente de perfil los pide uno a uno y los guarda:

| Dato | Para qué | ¿Obligatorio? |
|---|---|---|
| Nombre o apodo | cómo llamarte | Sí |
| Sexo | ecuación de BMR | Sí |
| Edad | ecuación de BMR | Sí |
| Altura | ecuación de BMR | Sí |
| Peso actual | BMR, TDEE y tendencia | Sí |
| % de grasa | Katch-McArdle | No (se estima con Deurenberg) |
| Sesiones de entrenamiento/semana | actividad real | No |
| Pasos al día (NEAT) | actividad real | No |
| Tipo de trabajo | NEAT de fondo | No |
| Actividad general | factor de actividad | Sí |
| Objetivo y peso objetivo | el plan | Sí |

Los datos opcionales se pueden **saltar**: el bot nunca bloquea el perfil.

El factor de actividad es la variable que más pesa (cambiar de nivel mueve
270–600 kcal/día, más que la diferencia entre ecuaciones), así que se afina
con tus pasos y tus sesiones: ~35 kcal por cada 1 000 pasos de diferencia y
~3% del BMR por sesión de diferencia.

### Tu objetivo manda: el bot informa, no impone

El bot **calcula y explica**, pero la decisión es tuya. Con `/objetivo` (o el
botón 🎯 de «Mi semana») puedes fijar tus propias calorías:

```
/objetivo 2200
→ Eso son 200 kcal menos que tu mantenimiento (2400): ~0,18 kg por semana.
  ¿Lo confirmas?
```

El bot te dice siempre qué significa en calorías y en kg/semana, y **solo se
niega a bajar del suelo de seguridad** (1200 mujer / 1500 hombre / 1400 resto).
`/objetivo 0` (o el botón «Volver al automático») devuelve el control al
cálculo automático.

#### Qué dicen las fuentes

| Recomendación | Fuente | Qué hace el bot |
|---|---|---|
| «La ingesta no debe bajar de 1.200 kcal (mujeres) o 1.500 (hombres) salvo supervisión» | Harvard Health, *Calorie counting made easy* | Suelo duro: por debajo no se pasa |
| «Los pacientes que comen menos de 1.200 kcal/día pueden tener riesgo nutricional y necesitan suplementación y supervisión médica» | Alberta Health Services, *Nutrition Guideline: Adult Obesity Care* (2020) | Suelo duro + aviso explícito |
| 1.200/1.500 (mujeres) y 1.500/1.800 (hombres) como rango de programa clínico supervisado | NHLBI (1998), AHA/ACC/TOS (2013), NICE NG246 (2025) | No son «objetivos», son barandillas de seguridad |
| «Un déficit de 500–750 kcal sobre el baseline» (o 500–1.000) | Alberta Health Services; Harvard Health | Es lo que sugiere el bot por defecto |
| La adaptación termogénica promedia ~120 kcal/día, con mucha variabilidad individual; se atenúa o desaparece tras estabilizar el peso | Müller 2013; revisión sistemática *Br J Nutr* 2022 (33 estudios, 2.528 personas) | De ahí que el bot calibre con tus datos reales y no solo con la fórmula |
| Con 25% de restricción, el TDEE cae ~450 kcal/día a los 3 meses, parte por adaptación metabólica | CALERIE (Redman 2009, *PLOS One*) | De ahí que el déficit grande avise |
| Pausas a mantenimiento: mismo resultado y **menos** caída de TDEE | MATADOR (Byrne 2017, *Int J Obes*); meta-análisis *Nutrition Reviews* 2024 (12 ECAs, 881 personas) | El modelo semanal con margen: puedes comer a mantenimiento unos días |
| Flexible vs. rígido: sin diferencias significativas en pérdida ni en mantenimiento | *JISSN* 2021; *Int J Obes* 2020 (24 meses) | El banco semanal reparte: un día de más no rompe la semana |

> «Los suelos son barandillas, no objetivos»: no escalan con el tamaño del
> cuerpo. Por eso el bot nunca te *obliga* a un número, pero tampoco te deja
> comiendo por debajo de lo seguro sin explicarte por qué.

### La barra del acumulado

El total no se recalcula cada vez: **cada semana se cierra** y su resultado se
suma (si ahorraste) o se resta (si te pasaste) al contador. Así el acumulado
sube y baja con el tiempo y es la medida real de lo que has hecho, no una
estimación del momento.

```
📊 Acumulado
▰▱▱▱▱▱▱▱▱▱▱▱▱▱  2.834/50.050 kcal
Eso son 0,37 kg de grasa ahorrados en 4 semanas.

Semana a semana
  26/10 → +1.220 kcal · total 1.220 kcal
  02/11 → −1.104 kcal 🔴 · total 116 kcal
  09/11 → +1.896 kcal · total 2.012 kcal
  16/11 → +822 kcal · total 2.834 kcal
  23/11 → +0 kcal · total 2.834 kcal (en curso)
```

Detalles:

- El objetivo de la barra son las kcal que faltan para tu peso objetivo
  (`(peso actual − peso objetivo) × 7.000`).
- **La equivalencia es 1 kg de grasa ≈ 7.000 kcal** (la regla que usa el
  bot). Es una aproximación: el tejido adiposo es ~87% lípido, así que 1 kg
  de grasa pura valdría más, y el peso del cuerpo no es solo grasa (agua,
  glucógeno, contenido intestinal, músculo). Por eso la equivalencia sirve
  para ver la **tendencia**, no para esperar un número exacto en la báscula.
- El bot recuerda este aviso en `/meta` y en `/semana`: el peso fluctúa por
  el agua y el glucógeno (sal, carbohidratos, entrenamiento), por el
  contenido intestinal, por la hora del día y por el ciclo menstrual. Lo que
  cuenta es la **media de los pesos de dos semanas**, que es exactamente lo
  que usa la tendencia.
- Cada semana se guarda con el **mantenimiento de entonces** (el medio de los
  objetivos de esos días), no con el de hoy: tu peso y tu actividad cambian.
- La semana en curso se muestra aparte y todavía puede moverse.
- Una semana **sin registros no cuenta**: sin datos no se inventa nada.
- El cierre semanal también se borra con `/olvidarme`.

### Salvaguardas

- **Suelo de seguridad**: nunca por debajo de 1200 kcal (mujer) / 1500 (hombre) / 1400 (otro).
- **Techo del déficit automático**: máximo 25% del mantenimiento semanal (un déficit mayor frena la pérdida y no se sostiene). Si lo eliges tú, se acepta con aviso.
- **Macros**: proteína 1.8 g/kg al adelgazar, grasa 25–30% de las kcal, carbohidratos el resto.
- **Tendencia de peso**: regresión lineal de los últimos 14 días → kg/semana, ETA y proyección a 4 semanas.
- **Detección de meseta**: si la tendencia es ~0 durante más de una semana, avisa de los errores típicos
  (aceites y porciones).

> Ninguna fórmula de BMR es una medición: la precisión real está en ±10-20%.
> La forma de acercarse es mirar la tendencia de peso, y eso es justo lo que
> hace el bot con los datos que le das.

### 🕒 El día del usuario

Todas las marcas de tiempo se guardan en UTC, pero el «hoy» de cada usuario se
calcula en su zona horaria (`TIMEZONE`): si son las 00:30 en Madrid, sus
comidas de esta madrugada ya cuentan como hoy. La racha de días también se
cuenta en hora local.

---

## 🗂️ Estructura

```
openPic/
├── foodbot/
│   ├── config.py              # configuración (lee .env)
│   ├── main.py                # arranque de los dos bots  (--check)
│   ├── core/
│   │   ├── db.py              # SQLite: usuarios, tokens, comidas, pesos, uso, cache
│   │   ├── nutrition.py       # BMR/TDEE/deficit/macros/tendencia
│   │   ├── foodr1.py          # prompts + parser + cliente HTTP (llama.cpp/Ollama/mock)
│   │   ├── tokens.py          # emisión, canje y revocación de tokens
│   │   ├── users.py           # perfil, objetivos, resúmenes, progreso
│   │   ├── limits.py          # cuotas por plan (fotos, mensajes, tamaño)
│   │   ├── privacy.py         # retención de fotos y borrado de datos
│   │   ├── timeutil.py        # zona horaria, franjas horarias, formato
│   │   └── text.py            # formato de mensajes y barras de progreso
│   ├── data/ingredients.py    # tabla de ~170 alimentos + conversiones de unidades
│   └── bots/
│       ├── gate.py            # bot privado (un solo usuario)
│       └── public.py          # bot público
├── scripts/
│   ├── download_model.py      # descarga GGUF con resume y checksum
│   ├── setup_llama.sh         # compila llama.cpp (vulkan/cuda/cpu)
│   ├── run_llama_server.sh    # arranca el servidor con Food-R1
│   ├── wait_for_engine.sh     # espera a que el motor esté listo
│   └── install_autostart.sh   # instala los servicios de usuario (arranque automático)
├── deploy/
│   ├── llama-server.service   # servicio del sistema (con sudo)
│   ├── foodbot.service        # servicio del sistema (con sudo)
│   ├── llama-server.user.service   # servicio de usuario (sin sudo)
│   └── foodbot.user.service        # servicio de usuario (sin sudo)
└── tests/                     # 189 tests + 11 de integración con el modelo real
```

## 🧪 Tests

```bash
python -m pytest tests/ -q
```

Cubren el parser de Food-R1, el motor nutricional, el ciclo de vida de los
tokens, el flujo de usuario de extremo a extremo, la construcción de los bots
y, además:

| Fichero | Qué garantiza |
|---|---|
| `test_isolation.py` | Un usuario no ve ni toca los datos de otro |
| `test_privacy.py` | Las fotos no se quedan en disco y el borrado es real |
| `test_limits.py` | Las cuotas por plan se aplican de verdad |
| `test_timeutil.py` | El «hoy» es el del usuario, no el de UTC |
| `test_gate_bot.py` | El panel de tokens es de un solo usuario |
| `test_public_bot.py` | Flujos completos: foto, corrección, caché, borrado |
| `test_client_queue.py` | Las inferencias se serializan y los prompts formatean |
| `test_config.py` | Lectura del `.env` y valores por defecto |
| `test_db.py` | Cache, alias y serialización |

### Tests contra el modelo real

`tests/test_live_model.py` no usa el proveedor `mock`: habla con el
`llama-server` de verdad y con una foto real. Comprueba que el modelo
mantiene el formato `<answer>…</answer>`, que las cifras del mensaje cuadran
entre sí (4P + 4C + 9G = kcal), que la cola serializa las peticiones y que el
flujo completo del bot (foto → respuesta → fila en la BD → foto borrada de
disco) funciona con inferencia real.

```bash
NGL=0 scripts/run_llama_server.sh q8_0     # en otra terminal

# La foto de prueba no está en el repo (es un binario): pon la tuya
cp ~/Imagens/comida.jpg data/test_paella.jpg
# ...o indica la ruta: TEST_IMAGE=~/Imagens/comida.jpg

python -m pytest tests/test_live_model.py -v -m live
```

Si el motor no está en marcha, **los tests se saltan solos** (no fallan), así
que la suite normal sigue siendo verde en cualquier máquina. Son lentos: en
CPU una foto tarda alrededor de un minuto.

---

## ⚠️ Límites y responsabilidades

- Las calorías son **estimaciones visuales**: una foto no revela el aceite oculto,
  la salsa ni el peso real de la porción.
- No usar para dosificar insulina, alergias, trastornos alimentarios ni
  decisiones clínicas. El propio repo del modelo advierte de esto.
- Los datos de los usuarios (peso, fotos, consumos) se guardan en `data/foodbot.db`
  en local: añade cifrado o una BD gestionada si vas a operar a escala.
