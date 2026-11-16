/* ObsVoiceClip - overlay */
(() => {
  "use strict";

  const params = new URLSearchParams(window.location.search);
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const root = $("#overlay-root");
  let state = null;
  let lastClipKey = "";
  let toastTimer = 0;
  let source = null;
  let monthDay = "";

  function fmtClock(ts) {
    try {
      return new Date(ts * 1000).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" });
    } catch (_) { return ""; }
  }

  function setPosition(position) {
    const allowed = ["top-right", "top-left", "bottom-right", "bottom-left"];
    const value = allowed.includes(position) ? position : "top-right";
    root.className = `pos-${value}`;
  }

  function setHidden(el, hidden) {
    if (!el) return;
    el.hidden = !!hidden;
    if (hidden) el.setAttribute("hidden", ""); else el.removeAttribute("hidden");
  }

  function applyState(data) {
    state = data || {};
    const settings = state.settings || {};
    const voice = state.voice || {};
    const clip = state.clip || {};
    const overlay = state.overlay || {};
    setPosition(params.get("position") || overlay.position || settings.overlay_position || "top-right");

    const card = $("#voice-card");
    setHidden(card, overlay.show_voice === false);

    const voiceState = String(voice.state || "off");
    const title = $("#voice-title");
    const sub = $("#voice-sub");
    const dot = $("#voice-dot");
    let listening = false;
    let titleText = "VoiceClip";
    let subText = "Voz desactivada";

    if (voiceState === "listening") {
      listening = true;
      titleText = clip.phase === "saving" || clip.phase === "uploading" ? "Guardando clip…" : "Escuchando";
      subText = voice.partial && overlay.show_partial !== false
        ? `“${voice.partial}”`
        : (voice.device ? `Di “clip” · ${voice.device}` : "Di “clip” para guardar el momento");
    } else if (voiceState === "starting" || voiceState === "loading") {
      titleText = "Cargando voz…";
      subText = "Preparando el reconocimiento local";
    } else if (voiceState === "model_missing") {
      titleText = "Falta el modelo";
      subText = "Descárgalo desde el panel de VoiceClip";
    } else if (voiceState === "error") {
      titleText = "Voz con error";
      subText = voice.error || "Revisa el panel de VoiceClip";
    } else if (clip.phase === "saving" || clip.phase === "uploading") {
      titleText = "Guardando clip…";
      subText = "Procesando los últimos segundos";
    }

    if (title) title.textContent = titleText;
    if (sub) {
      sub.textContent = subText;
      sub.classList.toggle("partial", listening && !!voice.partial);
    }
    if (dot) dot.classList.toggle("listening", listening);

    const level = Math.max(0, Math.min(1, Number(voice.level || 0)));
    const active = listening && (voice.speaking || level > 0.015);
    const meter = $("#voice-meter");
    if (meter) {
      meter.classList.toggle("active", active);
      $$("span", meter).forEach((bar, index) => {
        const base = 0.10 + 0.04 * ((index * 5) % 4);
        const wave = 0.22 + 0.78 * Math.abs(Math.sin((index / 6) * Math.PI));
        const height = active ? Math.min(1, base + level * wave * 1.9) : 0.14;
        bar.style.height = `${Math.round(height * 100)}%`;
      });
    }

    const connection = $("#connection");
    if (connection) {
      connection.textContent = `LOCAL · VOICECLIP · ${fmtClock(state.now || 0)}`;
    }

    const controls = $("#controls");
    const showControls = params.get("controls") === "1" || params.get("controls") === "true";
    setHidden(controls, !showControls);
    if (showControls) renderControls(settings, clip);

    maybeShowToast(clip);
  }

  function renderControls(settings, clip) {
    const select = $("#overlay-duration");
    if (select) {
      const values = Array.from(new Set([...(settings.durations || []), Number(settings.duration || 30)]))
        .filter((value) => Number.isFinite(Number(value)))
        .sort((a, b) => a - b);
      const signature = values.join(",");
      if (select.dataset.signature !== signature) {
        select.dataset.signature = signature;
        select.innerHTML = values
          .map((seconds) => `<option value="${Number(seconds)}">${Number(seconds)} s</option>`)
          .join("");
      }
      if (document.activeElement !== select) select.value = String(settings.duration || values[0] || 30);
    }
    const button = $("#overlay-save");
    if (button) {
      const busy = ["saving", "saved", "uploading"].includes(String(clip.phase || ""));
      button.disabled = busy;
      button.textContent = busy ? "Guardando…" : "Guardar clip";
    }
  }

  function maybeShowToast(clip) {
    const phase = String(clip.phase || "idle");
    if (!["done", "error"].includes(phase)) return;
    const key = `${clip.saved_at || 0}:${phase}:${clip.url || ""}:${clip.error || ""}`;
    if (key === lastClipKey) return;
    const age = Date.now() / 1000 - Number(clip.uploaded_at || clip.saved_at || 0);
    if (age > 12) return;
    lastClipKey = key;
    const toast = $("#clip-toast");
    if (!toast) return;
    const kicker = $("#toast-kicker");
    const title = $("#toast-title");
    const link = $("#toast-link");
    const isError = phase === "error";
    toast.classList.toggle("error", isError);
    setHidden(toast, false);
    if (kicker) kicker.textContent = isError ? "ERROR DE CLIP" : (clip.url ? "CLIP EN GITHUB" : "CLIP GUARDADO");
    if (title) {
      if (isError) {
        title.textContent = clip.error || "No se pudo procesar el clip";
      } else {
        const bits = [];
        if (clip.duration) bits.push(`${clip.duration} s`);
        if (clip.size) bits.push(`${(Number(clip.size) / 1024 / 1024).toFixed(1)} MB`);
        title.textContent = clip.url ? `Clip subido${bits.length ? ` · ${bits.join(" · ")}` : ""}` : `Clip guardado${bits.length ? ` · ${bits.join(" · ")}` : ""}`;
      }
    }
    if (link) {
      if (clip.url && !isError) {
        link.href = clip.url;
        link.textContent = clip.url;
        setHidden(link, false);
      } else {
        setHidden(link, true);
      }
    }
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => setHidden(toast, true), isError ? 7000 : 9000);
  }

  async function api(path, body) {
    const opts = {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-VoiceClip": "1" },
      body: JSON.stringify(body || {}),
    };
    const response = await fetch(path, opts);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  function bindControls() {
    $("#overlay-save")?.addEventListener("click", async () => {
      try { await api("/api/clip", { voice_text: "overlay" }); } catch (_) { /* noop */ }
    });
    $("#overlay-duration")?.addEventListener("change", async (event) => {
      try { await api("/api/duration", { seconds: Number(event.target.value) }); } catch (_) { /* noop */ }
    });
  }

  function connect() {
    if (!("EventSource" in window)) return;
    if (source) source.close();
    source = new EventSource("/api/events");
    source.addEventListener("state", (event) => {
      try { applyState(JSON.parse(event.data)); } catch (_) { /* noop */ }
    });
    source.onerror = () => {
      const connection = $("#connection");
      if (connection) connection.textContent = "RECONECTANDO…";
    };
  }

  async function init() {
    document.body.classList.toggle("preview", params.has("preview"));
    setPosition(params.get("position") || "top-right");
    bindControls();
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (response.ok) applyState(await response.json());
    } catch (_) { /* noop */ }
    connect();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
