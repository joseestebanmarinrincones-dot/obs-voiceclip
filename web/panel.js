/* ObsVoiceClip - panel */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));

  let state = null;
  let devices = [];
  let localNotices = [];
  let dismissedUpdate = "";
  let settingsTimer = 0;
  let lastNotices = "";
  let lastHistory = "";
  let lastRender = 0;
  let eventSource = null;

  const isDock = () => {
    const params = new URLSearchParams(window.location.search);
    return params.has("dock") || window.innerWidth < 640;
  };

  async function api(path, body, method) {
    const opts = {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: { "X-VoiceClip": "1" },
      cache: "no-store",
    };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const response = await fetch(path, opts);
    let data = null;
    try { data = await response.json(); } catch (_) { /* noop */ }
    if (!response.ok) {
      const message = (data && (data.error || data.message)) || `Error HTTP ${response.status}`;
      throw new Error(message);
    }
    return data || {};
  }

  function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value == null ? "" : String(value);
  }

  function setValue(el, value) {
    if (!el) return;
    if (document.activeElement === el) return;
    const next = value == null ? "" : String(value);
    if (el.value !== next) el.value = next;
  }

  function setChecked(el, value) {
    if (!el) return;
    if (document.activeElement === el) return;
    el.checked = !!value;
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.hidden = !!hidden;
    if (hidden) el.setAttribute("hidden", ""); else el.removeAttribute("hidden");
  }

  function fmtTime(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
    } catch (_) { return "—"; }
  }

  function fmtDate(ts) {
    if (!ts) return "—";
    try {
      return new Date(ts * 1000).toLocaleString("es-ES", {
        day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
      });
    } catch (_) { return "—"; }
  }

  function humanSize(size) {
    let value = Number(size || 0);
    if (!value) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i += 1; }
    return i === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[i]}`;
  }

  function notify(text, kind = "info") {
    const id = `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    localNotices.unshift({
      id, text, kind, at: Date.now() / 1000, url: "", local: true,
    });
    localNotices = localNotices.slice(0, 6);
    renderNotices();
    setTimeout(() => {
      localNotices = localNotices.filter((item) => item.id !== id);
      renderNotices();
    }, 9000);
  }

  function setLocalError(error) {
    const message = error && error.message ? error.message : String(error || "Error desconocido");
    notify(message, "error");
  }

  // -----------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------

  function render() {
    if (!state) return;
    const now = performance.now();
    if (now - lastRender < 30) return;
    lastRender = now;
    document.body.classList.toggle("dock", isDock());

    const settings = state.settings || {};
    const replay = state.replay || {};
    const voice = state.voice || {};
    const github = state.github || {};
    const clip = state.clip || {};
    const update = state.update || {};
    const model = state.model || {};
    const obsState = state.obs || {};

    setText("version", `v${state.version || "1.0.0"}`);
    setText("footer-version", `v${state.version || "1.0.0"}`);
    setText("profile-pill", obsState.profile || "sin perfil");
    setText("duration-big", settings.duration || 30);
    setText("panel-url", (state.server || {}).panel_url || "—");
    setText("log-path", state.log_path || "—");
    setText("data-path", state.log_path ? String(state.log_path).replace(/[\\/][^\\/]+$/, "") : "—");

    renderTopStatus(settings, replay, voice, github, update);
    renderUpdate(update, settings);
    renderClip(settings, replay, clip, obsState);
    renderLastClip(clip);
    renderVoice(settings, voice, model);
    renderModel(model);
    renderGithub(settings, github);
    renderOverlay(settings);
    renderNotices();
    renderHistory();
    renderMisc(settings, replay, update);
  }

  function renderTopStatus(settings, replay, voice, github, update) {
    const pills = [];
    if (replay.active) {
      pills.push('<span class="pill ok"><span class="dot"></span>Replay activo</span>');
    } else if (replay.restart_required) {
      pills.push('<span class="pill warn"><span class="dot"></span>Reinicia OBS</span>');
    } else {
      pills.push('<span class="pill bad"><span class="dot"></span>Replay detenido</span>');
    }
    if (voice.state === "listening") {
      pills.push('<span class="pill ok"><span class="dot"></span>Voz lista</span>');
    } else if (voice.enabled && ["starting", "loading"].includes(voice.state)) {
      pills.push('<span class="pill warn"><span class="dot"></span>Cargando voz</span>');
    } else if (voice.state === "model_missing") {
      pills.push('<span class="pill warn"><span class="dot"></span>Falta modelo</span>');
    } else if (voice.enabled && voice.state === "error") {
      pills.push('<span class="pill bad"><span class="dot"></span>Voz con error</span>');
    } else {
      pills.push('<span class="pill"><span class="dot"></span>Voz apagada</span>');
    }
    if (github.configured) {
      pills.push(`<span class="pill ${github.verified ? "ok" : "warn"}"><span class="dot"></span>${github.verified ? "GitHub listo" : "GitHub sin verificar"}</span>`);
    } else {
      pills.push('<span class="pill"><span class="dot"></span>GitHub sin configurar</span>');
    }
    if (update.available) {
      pills.push('<span class="pill warn"><span class="dot"></span>Actualización</span>');
    }
    const top = $("#top-status");
    if (top && top.dataset.signature !== pills.join("")) {
      top.dataset.signature = pills.join("");
      top.innerHTML = pills.join("");
    }
  }

  function renderUpdate(update, settings) {
    const banner = $("#update-banner");
    if (!banner) return;
    const latest = String(update.latest || "");
    const available = !!update.available && latest && latest !== dismissedUpdate;
    if (!available) {
      setHidden(banner, true);
      return;
    }
    setHidden(banner, false);
    setText("update-title", `Actualización v${latest} disponible`);
    setText(
      "update-sub",
      update.installing
        ? `Instalando… ${Math.round(Number(update.progress || 0))}%`
        : "Hay una versión nueva. Puedes instalarla ahora y reiniciar OBS después."
    );
    const install = $("#update-install");
    if (install) {
      install.disabled = !!update.installing;
      install.textContent = update.installing ? "Instalando…" : "Actualizar";
    }
  }

  function renderClip(settings, replay, clip, obsState) {
    const select = $("#duration-select");
    if (select) {
      const values = Array.from(new Set([...(settings.durations || []), Number(settings.duration || 30)]))
        .filter((v) => Number.isFinite(Number(v)))
        .sort((a, b) => a - b);
      const signature = values.join(",");
      if (select.dataset.signature !== signature) {
        select.dataset.signature = signature;
        select.innerHTML = values
          .map((seconds) => `<option value="${Number(seconds)}">${Number(seconds)} segundos</option>`)
          .join("");
      }
      setValue(select, String(settings.duration || values[0] || 30));
    }

    const phase = String(clip.phase || "idle");
    const phasePill = $("#clip-phase-pill");
    const phaseText = $("#clip-phase");
    if (phasePill && phaseText) {
      let label = "Listo";
      let cls = "pill";
      if (phase === "saving") { label = "Guardando…"; cls = "pill warn"; }
      else if (phase === "saved") { label = "Procesando…"; cls = "pill warn"; }
      else if (phase === "uploading") { label = "Subiendo…"; cls = "pill warn"; }
      else if (phase === "done") { label = clip.url ? "Subido" : "Guardado"; cls = "pill ok"; }
      else if (phase === "error") { label = "Error"; cls = "pill bad"; }
      phasePill.className = cls;
      phasePill.innerHTML = `<span class="dot"></span><span id="clip-phase">${esc(label)}</span>`;
    }

    const save = $("#save-clip");
    if (save) {
      const busy = ["saving", "saved", "uploading"].includes(phase);
      save.disabled = busy;
      save.textContent = busy
        ? (phase === "uploading" ? "Subiendo clip…" : "Guardando clip…")
        : "Guardar clip ahora";
    }

    const active = !!replay.active;
    const restart = !!replay.restart_required;
    const line = $("#replay-status");
    if (line) {
      let cls = "status-line";
      let text = "";
      if (restart) {
        cls += " warn";
        text = "Replay Buffer configurado. Reinicia OBS una vez para activarlo.";
      } else if (!replay.output_exists) {
        cls += " warn";
        text = "Replay Buffer no disponible. Pulsa “Aplicar duración y reiniciar” y reinicia OBS.";
      } else if (active) {
        cls += " ok";
        text = `Replay Buffer activo · ${settings.duration || 30} s · ${obsState.streaming ? "emitiendo" : obsState.recording ? "grabando" : "en espera"}`;
      } else {
        cls += " warn";
        text = "Replay Buffer detenido. Se iniciará al emitir, grabar o al guardar un clip.";
      }
      line.className = cls;
      line.innerHTML = `<span class="dot"></span><span>${esc(text)}</span>`;
    }
    setText("replay-caption", active ? "Replay Buffer activo · OBS listo" : restart ? "Necesita reiniciar OBS" : "Replay Buffer en espera");
    setHidden($("#restart-hint"), !restart);
  }

  function renderLastClip(clip) {
    const name = clip.filename || "";
    const path = clip.path || "";
    setText("last-clip-name", name || "Todavía no hay clips");
    if (!name) {
      setText("last-clip-info", "Di “clip” o pulsa Guardar clip.");
    } else {
      const bits = [];
      if (clip.size) bits.push(humanSize(clip.size));
      if (clip.duration) bits.push(`${clip.duration} s`);
      if (clip.uploaded_at) bits.push("GitHub");
      else if (clip.phase === "error" && clip.error) bits.push(clip.error);
      else if (clip.phase === "done") bits.push("local");
      setText("last-clip-info", `${bits.join(" · ")}${clip.error ? ` — ${clip.error}` : ""}`);
    }
    const link = $("#last-clip-link");
    if (link) {
      if (clip.url) {
        link.href = clip.url;
        setHidden(link, false);
      } else {
        link.removeAttribute("href");
        setHidden(link, true);
      }
    }
    const upload = $("#upload-last");
    if (upload) upload.disabled = !path && !name;
  }

  function renderVoice(settings, voice, model) {
    setChecked($("#voice-toggle"), !!voice.enabled);
    const voiceState = String(voice.state || "off");
    const title = $("#voice-title");
    const sub = $("#voice-sub");
    let titleText = "Voz desactivada";
    let subText = "Actívala para usar comandos por voz.";
    let listening = false;
    if (voiceState === "listening") {
      titleText = "Escuchando";
      listening = true;
      subText = voice.partial ? voice.partial : (voice.device || "Di “clip” para guardar los últimos segundos.");
      if (voice.partial) subText = `“${voice.partial}”`;
    } else if (voiceState === "starting" || voiceState === "loading") {
      titleText = "Cargando modelo de voz";
      subText = "Preparando el reconocimiento local…";
    } else if (voiceState === "model_missing") {
      titleText = "Falta el modelo de voz";
      subText = "Pulsa “Descargar modelo” en esta tarjeta.";
    } else if (voiceState === "error") {
      titleText = "Error de voz";
      subText = voice.error || "Revisa el dispositivo y los logs.";
    } else if (!voice.enabled) {
      titleText = "Voz desactivada";
    }
    if (title) {
      title.innerHTML = `<span class="live-dot ${listening ? "listening" : ""}"></span>${esc(titleText)}`;
    }
    if (sub) {
      sub.textContent = subText;
      sub.classList.toggle("partial", listening && !!voice.partial);
    }
    const box = $("#voice-box");
    if (box) box.dataset.state = voiceState;

    const level = Math.max(0, Math.min(1, Number(voice.level || 0)));
    const active = listening && (voice.speaking || level > 0.015);
    const meter = $("#voice-meter");
    if (meter) {
      meter.classList.toggle("active", active);
      const bars = $$("span", meter);
      bars.forEach((bar, index) => {
        const base = 0.10 + 0.04 * ((index * 7) % 5);
        const wave = 0.18 + 0.82 * Math.abs(Math.sin((index / bars.length) * Math.PI));
        const height = active ? Math.min(1, base + level * wave * 1.8) : 0.12;
        bar.style.height = `${Math.round(height * 100)}%`;
      });
    }

    const device = $("#voice-device");
    if (device) {
      const current = String(settings.voice_device || "default");
      const options = [
        { id: "default", name: "Micrófono por defecto", note: "automático" },
        { id: "loopback", name: "Audio del sistema (loopback)", note: "WASAPI" },
        ...devices.map((item) => ({
          id: String(item.key || ("name:" + (item.raw_name || item.name || item.id))),
          name: String(item.name || item.id),
          note: item.loopback ? "loopback" : "entrada",
        })),
      ];
      const seen = new Set();
      const unique = options.filter((item) => {
        if (seen.has(item.id)) return false;
        seen.add(item.id);
        return true;
      });
      if (current && !unique.some((item) => item.id === current)) {
        unique.push({ id: current, name: settings.voice_device_name || `Dispositivo ${current}`, note: "actual" });
      }
      const signature = unique.map((item) => `${item.id}:${item.name}`).join("|");
      if (device.dataset.signature !== signature) {
        device.dataset.signature = signature;
        device.innerHTML = unique
          .map((item) => `<option value="${esc(item.id)}">${esc(item.name)}</option>`)
          .join("");
      }
      setValue(device, current);
    }

    const language = $("#voice-language");
    setValue(language, settings.voice_language || "es");

    const sensitivity = $("#voice-sensitivity");
    setValue(sensitivity, settings.voice_sensitivity ?? 55);
    setText("voice-sensitivity-value", settings.voice_sensitivity ?? 55);

    const phrases = $("#voice-phrases");
    if (phrases && document.activeElement !== phrases && !phrases.dataset.dirty) {
      phrases.value = (settings.voice_phrases || []).join(", ");
    }

    const deviceHint = $("#voice-device-hint");
    if (deviceHint) {
      deviceHint.textContent = voice.enabled && voiceState === "listening"
        ? "Escuchando el dispositivo seleccionado."
        : "Puedes elegir un micrófono o el audio del sistema.";
    }

    setText("model-status", model.ready
      ? `Modelo listo · ${model.label || model.name || ""}`
      : model.downloading
        ? `Descargando modelo… ${Math.round(Number(model.progress || 0))}%`
        : model.error
          ? `Modelo: ${model.error}`
          : `Modelo no descargado · ${model.label || "Español"}`);
    const modelStatus = $("#model-status");
    if (modelStatus) {
      modelStatus.className = `status-line ${model.ready ? "ok" : model.error ? "bad" : "warn"}`;
    }
    const download = $("#model-download");
    if (download) {
      download.disabled = !!model.downloading;
      download.textContent = model.ready ? "Modelo instalado" : "Descargar modelo";
    }
  }

  function renderModel(model) {
    const wrap = $("#model-progress-wrap");
    const label = $("#model-progress-label");
    const bar = $("#model-progress");
    const downloading = !!model.downloading;
    setHidden(wrap, !downloading);
    setHidden(label, !downloading);
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, Number(model.progress || 0)))}%`;
    setText("model-progress-text", model.downloading
      ? `${humanSize(model.downloaded || 0)} de ${humanSize(model.total || 0)}`
      : "");
    setText("model-progress-pct", model.downloading ? `${Math.round(Number(model.progress || 0))}%` : "");
  }

  function renderGithub(settings, github) {
    const configured = !!settings.github_configured;
    const verified = !!github.verified;
    const pill = $("#github-pill");
    if (pill) {
      pill.className = `pill ${verified ? "ok" : configured ? "warn" : ""}`;
      pill.innerHTML = `<span class="dot"></span><span>${verified ? "Conectado" : configured ? "Sin verificar" : "Sin configurar"}</span>`;
    }
    const token = $("#github-token");
    if (token) {
      token.placeholder = configured
        ? `Guardado (•••• ${settings.github_token_tail || "????"})`
        : "ghp_… o github_pat_…";
    }
    const hint = $("#github-token-hint");
    if (hint) {
      hint.textContent = configured
        ? "Token guardado localmente. Escribe uno nuevo para reemplazarlo."
        : "Crea un PAT clásico con permiso repo, o uno fino con Contents y Administration.";
    }
    const repo = $("#github-repo");
    setValue(repo, settings.github_repo || "obs-voiceclip-media");
    setChecked($("#github-public"), settings.github_public !== false);
    const link = $("#github-link");
    if (link) {
      if (settings.github_repo_url && settings.github_owner) {
        link.href = settings.github_repo_url;
        setHidden(link, false);
      } else {
        setHidden(link, true);
      }
    }
    const status = $("#github-status");
    if (status) {
      let cls = "status-line";
      let text = configured ? (verified ? `Conectado como ${settings.github_owner || "tu cuenta"}.` : "Token guardado. Pulsa “Guardar y conectar”.") : "Configura un token para subir clips.";
      if (verified) cls += " ok";
      else if (configured) cls += " warn";
      if (github.error) { cls = "status-line bad"; text = github.error; }
      status.className = cls;
      status.innerHTML = `<span class="dot"></span><span>${esc(text)}</span>`;
    }
    setChecked($("#auto-upload"), settings.auto_upload !== false);
  }

  function renderOverlay(settings) {
    const base = (state.server || {}).overlay_url || "";
    const sourceUrl = base ? `${base}?overlay=1` : "";
    const field = $("#overlay-url");
    if (field) setValue(field, sourceUrl);
    const open = $("#overlay-open");
    if (open) open.href = base ? `${base}?controls=1` : "#";
    setValue($("#overlay-position"), settings.overlay_position || "top-right");
    const frame = $("#overlay-preview");
    if (frame) {
      const next = `/overlay?preview=1&position=${encodeURIComponent(settings.overlay_position || "top-right")}`;
      if (frame.getAttribute("src") !== next) frame.setAttribute("src", next);
    }
  }

  function renderNotices() {
    const server = Array.isArray(state && state.notices) ? state.notices : [];
    const all = [...localNotices, ...server].slice(0, 8);
    const signature = JSON.stringify(all.map((n) => [n.id, n.text, n.kind, n.url]));
    if (signature === lastNotices) return;
    lastNotices = signature;
    const list = $("#notices-list");
    if (!list) return;
    list.innerHTML = "";
    for (const notice of all) {
      const row = document.createElement("div");
      row.className = `notice ${notice.kind || "info"}`;
      const dot = document.createElement("span");
      dot.className = "ico";
      const text = document.createElement("span");
      text.className = "text";
      text.textContent = notice.text || "";
      const time = document.createElement("span");
      time.className = "time";
      time.textContent = fmtTime(notice.at || 0);
      row.append(dot, text, time);
      if (notice.url) {
        const link = document.createElement("a");
        link.href = notice.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "Abrir";
        row.append(link);
      }
      list.append(row);
    }
  }

  function renderHistory() {
    const items = Array.isArray(state && state.history) ? state.history : [];
    const signature = JSON.stringify(items.slice(0, 8).map((item) => [item.path, item.url]));
    if (signature === lastHistory) return;
    lastHistory = signature;
    const list = $("#history-list");
    if (!list) return;
    list.innerHTML = "";
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "tiny muted";
      empty.textContent = "Aún no hay clips guardados.";
      list.append(empty);
    } else {
      for (const item of items.slice(0, 8)) {
        const row = document.createElement("div");
        row.className = "history-item";
        const name = document.createElement("span");
        name.className = "hist-name";
        name.textContent = item.filename || String(item.path || "").split(/[\\/]/).pop() || "clip";
        const meta = document.createElement("span");
        meta.className = "hist-meta";
        meta.textContent = `${item.duration || "?"} s · ${fmtDate(item.saved_at)}`;
        row.append(name, meta);
        if (item.url) {
          const link = document.createElement("a");
          link.href = item.url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          link.textContent = "GitHub";
          row.append(link);
        }
        list.append(row);
      }
    }
    setText("history-count", `${items.length} clip${items.length === 1 ? "" : "s"}`);
  }

  function renderMisc(settings, replay, update) {
    setChecked($("#auto-start"), settings.auto_start_replay !== false);
    setChecked($("#auto-update-check"), settings.auto_update_check !== false);
    const detail = $("#replay-detail");
    if (detail) {
      let cls = "status-line";
      let text = "Replay Buffer —";
      if (replay.restart_required) { cls += " warn"; text = "Falta reiniciar OBS una vez."; }
      else if (replay.active) { cls += " ok"; text = `Activo, ${settings.duration || 30} s configurados.`; }
      else if (replay.output_exists) { cls += " warn"; text = "Detenido. Puedes iniciarlo manualmente."; }
      else { cls += " warn"; text = "No disponible; aplica la duración y reinicia OBS."; }
      detail.className = cls;
      detail.innerHTML = `<span class="dot"></span><span>${esc(text)}</span>`;
    }
    const status = $("#update-status");
    if (status) {
      let cls = "status-line";
      let text = "Sin comprobar";
      if (update.installing) { cls += " warn"; text = `Instalando… ${Math.round(Number(update.progress || 0))}%`; }
      else if (update.available) { cls += " warn"; text = `v${update.latest} disponible`; }
      else if (update.error) { cls += " bad"; text = update.error; }
      else if (update.checked_at) { cls += " ok"; text = "Estás al día"; }
      status.className = cls;
      status.innerHTML = `<span class="dot"></span><span>${esc(text)}</span>`;
    }
  }

  // -----------------------------------------------------------------
  // Acciones
  // -----------------------------------------------------------------

  async function saveSettings(extra = {}, immediate = true) {
    if (!state) return;
    const body = {
      duration: Number($("#duration-select")?.value || state?.settings?.duration || 30),
      auto_start_replay: !!$("#auto-start")?.checked,
      auto_upload: !!$("#auto-upload")?.checked,
      auto_update_check: !!$("#auto-update-check")?.checked,
      voice_enabled: !!$("#voice-toggle")?.checked,
      voice_language: $("#voice-language")?.value || "es",
      voice_device: $("#voice-device")?.value || "default",
      voice_device_name: $("#voice-device")?.selectedOptions?.[0]?.textContent || "",
      voice_sensitivity: Number($("#voice-sensitivity")?.value || 55),
      voice_phrases: ($("#voice-phrases")?.value || "")
        .split(/[,;\n]+/).map((s) => s.trim()).filter(Boolean),
      github_repo: ($("#github-repo")?.value || "").trim(),
      github_public: !!$("#github-public")?.checked,
      overlay_position: $("#overlay-position")?.value || "top-right",
      ...extra,
    };
    const token = ($("#github-token")?.value || "").trim();
    if (token) body.github_token = token;
    try {
      await api("/api/settings", body);
      if (token) {
        const input = $("#github-token");
        if (input) input.value = "";
      }
    } catch (error) {
      setLocalError(error);
    }
  }

  function scheduleSettings(extra = {}, delay = 600) {
    window.clearTimeout(settingsTimer);
    settingsTimer = window.setTimeout(() => saveSettings(extra), delay);
  }

  async function loadDevices() {
    const select = $("#voice-device");
    if (select) {
      const current = select.value;
      select.innerHTML = '<option value="default">Micrófono por defecto</option><option value="loopback">Audio del sistema (loopback)</option>';
      select.value = current || "default";
    }
    try {
      const data = await api("/api/devices");
      devices = Array.isArray(data.devices) ? data.devices : [];
      if (data.ok === false && data.error) notify(data.error, "error");
      else if (!devices.length) notify("No se encontraron dispositivos de audio. ¿Está activado el helper?", "info");
      render();
    } catch (error) {
      setLocalError(error);
    }
  }

  async function copyText(text, label) {
    try {
      await navigator.clipboard.writeText(text);
      notify(`${label} copiado.`, "success");
    } catch (_) {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      try { document.execCommand("copy"); notify(`${label} copiado.`, "success"); }
      catch (error) { setLocalError(error); }
      area.remove();
    }
  }

  // -----------------------------------------------------------------
  // Eventos
  // -----------------------------------------------------------------

  function bind() {
    $("#duration-select")?.addEventListener("change", async (event) => {
      const seconds = Number(event.target.value);
      try {
        await api("/api/duration", { seconds });
        notify(`Duración: ${seconds} s. Aplicando al Replay Buffer…`, "info");
      } catch (error) { setLocalError(error); }
    });

    $("#save-clip")?.addEventListener("click", async () => {
      try { await api("/api/clip", { voice_text: "panel" }); }
      catch (error) { setLocalError(error); }
    });

    $("#voice-toggle")?.addEventListener("change", () => saveSettings());
    $("#auto-start")?.addEventListener("change", () => saveSettings());
    $("#auto-upload")?.addEventListener("change", () => saveSettings());
    $("#auto-update-check")?.addEventListener("change", () => saveSettings());
    $("#github-public")?.addEventListener("change", () => saveSettings());

    $("#voice-device")?.addEventListener("change", () => saveSettings({}, true));
    $("#voice-language")?.addEventListener("change", () => {
      const lang = $("#voice-language")?.value;
      const defaults = {
        es: ["clip", "guarda clip", "corta clip", "haz clip", "nuevo clip", "crea clip"],
        en: ["clip", "save clip", "make clip", "new clip", "create clip"],
      };
      const phrases = defaults[lang] || defaults.es;
      const area = $("#voice-phrases");
      if (area) { area.value = phrases.join(", "); delete area.dataset.dirty; }
      if (lang) api("/api/settings", { voice_language: lang, voice_phrases: phrases }).catch(setLocalError);
      notify("Cambiando idioma; si falta el modelo se descargará automáticamente.", "info");
    });
    $("#voice-sensitivity")?.addEventListener("input", (event) => {
      setText("voice-sensitivity-value", event.target.value);
      scheduleSettings({}, 400);
    });
    $("#voice-phrases")?.addEventListener("input", (event) => {
      event.target.dataset.dirty = "1";
      scheduleSettings({}, 700);
    });
    $("#voice-phrases")?.addEventListener("blur", (event) => {
      delete event.target.dataset.dirty;
    });

    $("#voice-devices-reload")?.addEventListener("click", loadDevices);
    $("#voice-restart")?.addEventListener("click", async () => {
      try { await api("/api/voice/restart", {}); notify("Reiniciando el reconocimiento de voz…", "info"); }
      catch (error) { setLocalError(error); }
    });

    $("#github-token-toggle")?.addEventListener("click", () => {
      const input = $("#github-token");
      if (!input) return;
      input.type = input.type === "password" ? "text" : "password";
    });

    $("#github-save")?.addEventListener("click", async () => {
      await saveSettings({}, true);
      notify("Guardando credenciales y conectando con GitHub…", "info");
      try { await api("/api/github/test", {}); }
      catch (error) { setLocalError(error); }
    });

    $("#github-clear")?.addEventListener("click", async () => {
      try {
        await api("/api/settings", { github_clear_token: true });
        notify("Token eliminado.", "success");
      } catch (error) { setLocalError(error); }
    });

    $("#upload-last")?.addEventListener("click", async () => {
      try { await api("/api/github/upload_last", {}); }
      catch (error) { setLocalError(error); }
    });

    $("#overlay-add")?.addEventListener("click", async () => {
      try { await api("/api/overlay/add", {}); notify("Overlay añadido a la escena actual.", "info"); }
      catch (error) { setLocalError(error); }
    });

    $("#copy-overlay")?.addEventListener("click", () => {
      const url = $("#overlay-url")?.value || "";
      if (url) copyText(url, "URL del overlay");
    });

    $("#copy-panel")?.addEventListener("click", () => {
      const url = (state && state.server && state.server.panel_url) || "";
      if (url) copyText(url, "URL del panel");
    });

    $("#copy-logs")?.addEventListener("click", () => {
      const path = state?.log_path || "";
      if (path) copyText(path, "Ruta de logs");
    });

    $("#overlay-position")?.addEventListener("change", () => saveSettings({}, true));

    $("#open-logs")?.addEventListener("click", () => api("/api/open", { target: "logs" }).catch(setLocalError));
    $("#open-clip-folder")?.addEventListener("click", () => api("/api/open", { target: "clip_folder" }).catch(setLocalError));

    $("#replay-start")?.addEventListener("click", async () => {
      const seconds = Number($("#duration-select")?.value || 30);
      try {
        await api("/api/duration", { seconds });
        notify("Iniciando Replay Buffer…", "info");
      } catch (error) { setLocalError(error); }
    });

    $("#replay-fix")?.addEventListener("click", async () => {
      const seconds = Number($("#duration-select")?.value || 30);
      try {
        await api("/api/duration", { seconds });
        notify("Aplicando duración y reiniciando el Replay Buffer…", "info");
      } catch (error) { setLocalError(error); }
    });

    $("#model-download")?.addEventListener("click", async () => {
      const language = $("#voice-language")?.value || "es";
      try {
        await api("/api/model/download", { language });
        notify("Descargando modelo de voz…", "info");
      } catch (error) { setLocalError(error); }
    });

    $("#check-update")?.addEventListener("click", async () => {
      try { await api("/api/update/check", {}); notify("Comprobando actualizaciones…", "info"); }
      catch (error) { setLocalError(error); }
    });

    $("#update-install")?.addEventListener("click", async () => {
      try { await api("/api/update/install", {}); notify("Descargando actualización…", "info"); }
      catch (error) { setLocalError(error); }
    });

    $("#update-later")?.addEventListener("click", () => {
      dismissedUpdate = String(state?.update?.latest || "");
      render();
    });

    window.addEventListener("resize", () => document.body.classList.toggle("dock", isDock()));
  }

  function connectEvents() {
    if (!("EventSource" in window)) return;
    if (eventSource) eventSource.close();
    eventSource = new EventSource("/api/events");
    eventSource.addEventListener("state", (event) => {
      try {
        state = JSON.parse(event.data);
        render();
      } catch (error) { console.error(error); }
    });
    eventSource.onopen = () => {
      const top = $("#top-status");
      if (top && !state) top.innerHTML = '<span class="pill ok"><span class="dot"></span>Conectado</span>';
    };
    eventSource.onerror = () => {
      notify("Panel desconectado. Reintentando…", "error");
    };
  }

  async function init() {
    document.body.classList.toggle("dock", isDock());
    bind();
    try {
      state = await api("/api/state");
      render();
    } catch (error) {
      setLocalError(error);
    }
    connectEvents();
    loadDevices();
    setInterval(() => {
      if (state && !eventSource) return;
      // Latido de seguridad por si el SSE se queda colgado.
      if (state && Date.now() / 1000 - (state.now || 0) > 12) {
        api("/api/state").then((data) => { state = data; render(); }).catch(() => {});
      }
    }, 5000);
  }

  document.addEventListener("DOMContentLoaded", init);
})();
