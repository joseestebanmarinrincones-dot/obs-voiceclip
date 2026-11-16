# ObsVoiceClip

**Clips por voz para OBS Studio, con overlay y subida automática a GitHub.**

Di **“clip”** (o “guarda clip”, “corta clip”…) mientras estás emitiendo o grabando y el plugin guarda los últimos segundos del **Replay Buffer** de OBS. El clip se sube a un repositorio **público** de GitHub y aparece en un overlay transparente para el directo.

> Hecho para Windows 10/11 + OBS Studio 30 o superior. Reconocimiento de voz **local** con Vosk: el audio no se envía a ninguna nube.

---

## Qué incluye

- **Comando por voz local** con Vosk (español o inglés). No necesita Internet después de descargar el modelo.
- **Duración configurable** (10–300 s, o valor personalizado) desde un desplegable dentro de OBS.
- **Panel de control** en OBS como *Custom Browser Dock* y también accesible desde el navegador.
- **Overlay transparente** listo para añadir a la escena actual con un clic.
- **Clip desde lo que emites o grabas**: usa el Replay Buffer nativo de OBS, así que captura el programa (no una ventana aparte).
- **Subida a GitHub** como *release* en un repositorio público. Enlace público por clip.
- **Aviso de actualización al abrir OBS** y botón para instalar la nueva versión desde el panel.
- **Historial** de los últimos clips y estado en vivo (voz, nivel de audio, Replay Buffer, GitHub).
- **Logs** locales para diagnosticar problemas.

---

## Instalación rápida (Windows)

1. Descarga el ZIP de la última versión desde [Releases](https://github.com/joseestebanmarinrincones-dot/obs-voiceclip/releases).
2. Cierra **OBS Studio** completamente.
3. Extrae el ZIP y ejecuta **`Instalar.bat`**.
4. Abre OBS. El instalador:
   - copia el plugin a `%APPDATA%\obs-studio\scripts\ObsVoiceClip`,
   - configura Python 3.12 para OBS,
   - activa el Replay Buffer en el perfil actual,
   - registra el script en la colección de escenas,
   - añade el dock **VoiceClip**.
5. Si OBS avisa de que hace falta reiniciar para el Replay Buffer, ciérralo y ábrelo otra vez.
6. En el dock VoiceClip:
   - elige tu micrófono o el audio del sistema,
   - espera a que termine la descarga del modelo (~40 MB la primera vez),
   - pega tu token de GitHub y pulsa **Guardar y conectar**,
   - pulsa **Añadir a la escena actual** para crear el overlay.

> Si no aparece el dock: **Ver → Docks → Docks de navegador personalizados**, título `VoiceClip`, URL `http://127.0.0.1:17877/panel?dock=1`.
>
> Si el script no aparece cargado: **Herramientas → Scripts → +** y selecciona `%APPDATA%\obs-studio\scripts\ObsVoiceClip\obs_voice_clip.py`.

### Requisitos

- Windows 10/11 de 64 bits.
- OBS Studio 30 o superior (probado en OBS 33).
- Python **3.12** de 64 bits instalado. El instalador intenta encontrarlo y configurar OBS automáticamente.
- El helper de voz (`voice_helper.exe`) ya viene incluido en el ZIP; no necesitas instalar Vosk ni PyAudio aparte.

---

## Uso

### Guardar un clip

- **Voz:** di `clip`, `guarda clip`, `corta clip`, `haz clip`, `nuevo clip`… (puedes cambiarlo en el panel).
- **Panel/dock:** botón **Guardar clip ahora**.
- **Overlay con controles:** abre `http://127.0.0.1:17877/overlay?controls=1` (útil como test o como dock).

El Replay Buffer debe estar activo. El plugin lo inicia automáticamente si está disponible. La duración del buffer es la duración del clip (aprox.; OBS corta en el keyframe anterior, así que puede variar ligeramente).

### Elegir el audio de la voz

En **Voz → Micrófono / audio**:

- **Micrófono por defecto** o un micrófono concreto: ideal para hablarle a OBS.
- **Audio del sistema (loopback)**: escucha lo que sale por los altavoces. Útil si quieres detectar la voz que ya forma parte de la emisión.

Si usas un micrófono, no hace falta que OBS lo monitorice. Vosk recibe el audio directamente del dispositivo.

### Overlay

El botón **Añadir a la escena actual** crea un *Browser Source* llamado `VoiceClip Overlay` que ocupa la escena y es transparente. Muestra:

- estado de la voz y nivel de audio,
- transcripción parcial mientras hablas,
- aviso de “Clip guardado” o “Clip subido” con enlace.

Puedes cambiar la esquina en **Overlay → Posición en pantalla**. Si prefieres añadirlo a mano, usa la URL:

```
http://127.0.0.1:17877/overlay?overlay=1
```

---

## GitHub: subir los clips

Los clips se publican como *release assets* en un repositorio **público**:

1. Crea un **Personal Access Token** en GitHub:
   - **Classic:** Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token. Marca el permiso **`repo`**.
   - **Fine-grained:** permite **Contents: Read and write** y **Administration: Read and write** (para crear el repositorio si no existe). Si no quieres dar Administration, crea el repositorio a mano y da Contents.
2. En el panel, pega el token y el nombre del repositorio (por defecto `obs-voiceclip-media`).
3. Deja marcado **Hacer público el repositorio** y pulsa **Guardar y conectar**.
4. El plugin crea el repositorio público si no existe y sube cada clip como release.

> **Aviso:** todo lo que subas será público. No uses esta función para clips con información privada. El token se guarda solo en tu PC, en `%LOCALAPPDATA%\ObsVoiceClip\config.json`; usa un token limitado y bórralo desde el panel cuando quieras.

---

## Actualizaciones

Al abrir OBS, el plugin consulta la última release de este repositorio. Si hay una versión nueva:

- muestra un mensaje al abrir OBS,
- ofrece instalarla desde el propio aviso,
- el panel también muestra un banner y el botón **Actualizar**.

La actualización descarga el ZIP de la release, reemplaza los archivos del plugin y pide reiniciar OBS. El helper de voz se detiene antes de reemplazarlo.

Para publicar una nueva versión:

1. Sube el número en `obs_voice_clip.py` (`VERSION`) y `manifest.json`.
2. Crea un tag `vX.Y.Z` y súbelo.
3. El workflow `.github/workflows/release.yml` empaqueta el helper con PyInstaller y publica el ZIP como release.

---

## Estructura del proyecto

```
obs_voice_clip.py      Script principal de OBS (Python, sin dependencias externas)
helper/voice_helper.py Helper de audio + Vosk (se empaqueta como EXE)
helper/voice_helper-1.0.2/ Helper compilado onedir (incluido en la release)
web/                   Panel y overlay (HTML/CSS/JS, sin librerías externas)
installer/             Instalador/desinstalador para Windows
tools/                 Scripts de build y empaquetado
.github/workflows/     Release automática
```

El script de OBS usa solo la librería estándar de Python. El helper habla con el plugin por HTTP en `127.0.0.1`; no abre puertos en la red local.

### Desarrollo

Requisitos: Python 3.12, PyInstaller y `pyaudiowpatch`.

```powershell
python -m pip install pyaudiowpatch pyinstaller vosk
powershell -ExecutionPolicy Bypass -File tools\build_helper.ps1
powershell -ExecutionPolicy Bypass -File tools\package.ps1
```

El script se puede probar directamente cargándolo desde **Herramientas → Scripts** en OBS. Los logs están en:

- `%LOCALAPPDATA%\ObsVoiceClip\logs\plugin.log`
- `%LOCALAPPDATA%\ObsVoiceClip\logs\voice_helper.log`

---

## Problemas frecuentes

### “Python no está cargado” en OBS

Abre **Herramientas → Scripts → Python Settings** y selecciona la carpeta de Python 3.12 (la que contiene `python.exe`). El instalador escribe la ruta en `%APPDATA%\obs-studio\user.ini` como `[Python] Path64bit`.

### “Replay Buffer no disponible”

OBS crea el Replay Buffer al arrancar según el perfil. Si el plugin lo activó por primera vez, cierra OBS y ábrelo otra vez. En **Ajustes → Salida → Repetición**, comprueba que está activado y que la duración es suficiente.

### La voz no detecta el comando

- Revisa el dispositivo en el panel; prueba el **audio del sistema** o tu micrófono.
- El modelo tarda en descargarse la primera vez; espera a que desaparezca “Descargando modelo”.
- Habla claro y prueba `clip` a secas.
- Mira `voice_helper.log`.

### GitHub devuelve 401/403

- El token no tiene permisos sobre el repositorio.
- En tokens clásicos, marca `repo`.
- Si el repositorio ya existe y es privado, el plugin intenta hacerlo público; necesitas permiso de administración. También puedes crearlo público a mano.

### El clip no se sube

Comprueba **GitHub → Guardar y conectar** y que **Subir clips automáticamente** esté marcado. El clip se guarda igualmente en disco; puedes subirlo con **Subir a GitHub** en “Último clip”.

### El overlay está en blanco

- Abre `http://127.0.0.1:17877/overlay?controls=1` en el navegador: si funciona, el problema es la URL del Browser Source.
- En OBS, marca **Cerrar cuando no sea visible** si el overlay no se actualiza al cambiar de escena, o recarga la fuente.
- El panel debe estar abierto/cargado para que el plugin esté activo.

---

## Privacidad y seguridad

- El audio se procesa **en tu PC** con Vosk. No se sube audio a ningún servidor de reconocimiento.
- El token de GitHub se guarda en texto plano en `%LOCALAPPDATA%\ObsVoiceClip\config.json`. Protégelo como cualquier credencial y revócalo si lo compartes.
- Los clips subidos son **públicos**.
- El panel escucha solo en `127.0.0.1` y exige una cabecera personalizada para las acciones; no se expone a Internet.
- El helper se ejecuta como proceso hijo de OBS y se cierra con OBS.

---

## Créditos y licencia

- [Vosk](https://alphacephei.com/vosk/) — reconocimiento de voz offline (Apache 2.0).
- [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) — captura WASAPI loopback (MIT/Apache 2.0).
- [OBS Studio](https://obsproject.com/) — API de scripting y Replay Buffer.

Código bajo licencia **MIT**. Consulta `LICENSE`.
