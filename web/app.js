/* app.js - يرسم الحالة ويستدعي الأوامر. لا يحسب شيئاً ولا يقرّر. */
"use strict";

const $ = (id) => document.getElementById(id);

/* ---- الشبكة ---- */
async function cmd(name, args) {
  try {
    const res = await fetch("/api/command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, args: args || [] }),
    });
    const data = await res.json();
    if (!res.ok) console.warn(name, data);
    return data;
  } catch (err) {
    console.warn("command failed", name, err);
    return null;
  }
}

/* ---- الحالة ---- */
let ST = null;
let logLen = 0;

function txt(id, value) {
  const el = $(id);
  if (el && el.textContent !== value) el.textContent = value;
}

function slider(id, outId, value, suffix) {
  const el = $(id);
  if (el && document.activeElement !== el) el.value = value;
  txt(outId, suffix ? value + suffix : value);
}

function check(id, value) {
  const el = $(id);
  if (el && el.checked !== value) el.checked = value;
}

function show(id, value) {
  const el = $(id);
  if (!el) return;
  const s = String(value || "");
  el.textContent = s;
  el.hidden = !s;
}

function draw(s) {
  ST = s;
  const e = s.engine, c = s.content, y = s.sync, m = s.mic, a = s.audio, n = s.now;

  /* الساعات */
  txt("simclock", n.sim_enabled ? `محاكاة ${n.sim}` : n.real);
  txt("realclock", n.sim_enabled ? "الحقيقي " + n.real : "");

  /* المحكّم */
  txt("btn-start", e.running ? "يعمل" : "ابدأ");
  $("btn-start").disabled = e.running;
  $("btn-stop").disabled = !e.running;
  $("meter-fill").style.width = Math.max(0, Math.min(100, e.db)) + "%";
  txt("dbline",
    `dB: ${e.db.toFixed(0)}  كلام=${e.is_speech ? 1 : 0}` +
    (e.is_speech !== e.is_speech_raw ? `  خام=${e.is_speech_raw ? 1 : 0}` : "") +
    `  (${e.player_backend})`);
  txt("stateline", e.state);
  txt("periodline", e.period);
  txt("prayerline", e.next_prayer ? "الصلاة التالية: " + e.next_prayer : "");
  show("playererr", e.player_error);

  /* الصوت */
  slider("in-vol", "out-vol", a.master_vol);
  check("in-mute", a.master_mute);
  slider("in-duck", "out-duck", a.duck_depth);
  check("in-athan", a.athan_enabled);

  /* الميكروفون */
  check("in-usemic", m.use_mic);
  slider("in-db", "out-db", m.manual_db);
  txt("micinfo", m.available ? `الواجهة: ${m.backend}` : "غير متاح");
  if (m.calibrating) txt("micinfo", "يعاير… صمت");

  /* المحتوى */
  check("in-contenabled", c.enabled);
  check("in-contentmute", c.muted);
  slider("in-cvol", "out-cvol", Math.round(c.volume * 100));
  txt("content-title", c.title);
  txt("content-state", c.state + (c.position && c.position !== "—" ? " · " + c.position : ""));
  const pct = parseFloat((c.position || "").match(/\((\d+)%\)/)?.[1] || "0");
  $("content-fill").style.width = (isNaN(pct) ? 0 : pct) + "%";
  txt("content-lib", `${c.count} مقطع · ${c.library}`);
  show("content-err", c.error);

  /* الخادم */
  if (document.activeElement !== $("in-url")) $("in-url").value = y.url || "";
  if (document.activeElement !== $("in-token")) $("in-token").value = y.token_set ? "••••" : "";
  if (document.activeElement !== $("in-parts")) $("in-parts").value = y.max_parts;
  if ($("in-window").options.length !== y.windows.length) {
    $("in-window").innerHTML = "";
    y.windows.forEach((w) => {
      const o = document.createElement("option");
      o.value = w; o.textContent = w;
      $("in-window").appendChild(o);
    });
  }
  $("in-window").value = y.window;
  $("btn-ssync").disabled = y.running;
  txt("sync-text", y.text || "");
  show("sync-note", y.note);
}

/* ---- السجل ---- */
async function pullLog() {
  try {
    const res = await fetch("/api/log?n=120");
    const data = await res.json();
    if (data.lines.length === logLen) return;
    logLen = data.lines.length;
    $("log").innerHTML = "";
    data.lines.forEach((line) => {
      const d = document.createElement("div");
      d.textContent = line;
      $("log").appendChild(d);
    });
    $("log").scrollTop = $("log").scrollHeight;
  } catch (_) { /* الخادم مغلق؛ البثّ سيعيد الاتصال */ }
}

/* ---- البثّ ---- */
function connect() {
  const src = new EventSource("/api/stream");
  src.onopen = () => txt("link", "متصل");
  src.onmessage = (ev) => {
    try { draw(JSON.parse(ev.data)); } catch (e) { console.warn(e); }
  };
  src.onerror = () => txt("link", "انقطع — يعيد المحاولة");
}

/* ---- الربط ---- */
function bind() {
  $("btn-start").onclick = () => cmd("start");
  $("btn-stop").onclick = () => cmd("stop");

  $("in-vol").oninput = (e) => {
    txt("out-vol", e.target.value);
    cmd("settings", [{ master_vol: Number(e.target.value) }]);
  };
  $("in-mute").onchange = (e) => cmd("settings", [{ master_mute: e.target.checked }]);
  $("in-duck").oninput = (e) => {
    txt("out-duck", e.target.value);
    cmd("settings", [{ duck_depth: Number(e.target.value) }]);
  };
  $("in-athan").onchange = (e) => cmd("settings", [{ athan_enabled: e.target.checked }]);
  document.querySelectorAll("[data-duck]").forEach((b) => {
    b.onclick = () => {
      txt("out-duck", b.dataset.duck);
      cmd("settings", [{ duck_depth: Number(b.dataset.duck) }]);
    };
  });

  $("in-usemic").onchange = (e) => cmd("settings", [{ use_mic: e.target.checked }]);
  $("btn-mic-list").onclick = async () => {
    const r = await cmd("mic_devices");
    if (!r || !Array.isArray(r.result)) return;
    const sel = $("in-device");
    sel.innerHTML = '<option value="">النظام الافتراضي</option>';
    r.result.forEach(([id, name]) => {
      const o = document.createElement("option");
      o.value = id; o.textContent = name;
      sel.appendChild(o);
    });
    if (ST && ST.mic.device !== null && ST.mic.device !== undefined) {
      sel.value = ST.mic.device;
    }
  };
  $("in-device").onchange = (e) =>
    cmd("mic_device", [e.target.value === "" ? null : Number(e.target.value)]);
  $("btn-calibrate").onclick = () => cmd("mic_calibrate");
  $("in-db").oninput = (e) => {
    txt("out-db", e.target.value);
    cmd("manual_db", [Number(e.target.value)]);
  };

  $("in-contenabled").onchange = (e) => cmd("settings", [{ content_enabled: e.target.checked }]);
  $("in-contentmute").onchange = (e) => cmd("settings", [{ content_muted: e.target.checked }]);
  $("in-cvol").oninput = (e) => {
    txt("out-cvol", e.target.value);
    cmd("settings", [{ content_volume: Number(e.target.value) / 100 }]);
  };
  $("btn-cplay").onclick = () => cmd("content_play");
  $("btn-cskip").onclick = () => cmd("content_skip");
  $("btn-crescan").onclick = () => cmd("content_rescan");
  $("btn-copen").onclick = () => cmd("content_open");

  $("in-url").onchange = (e) => cmd("settings", [{ content_sync_url: e.target.value.trim() }]);
  $("in-token").onchange = (e) => cmd("settings", [{ content_sync_token: e.target.value }]);
  $("in-parts").onchange = (e) => cmd("settings", [{ content_sync_max_parts: Number(e.target.value) }]);
  $("btn-sscan").onclick = () => cmd("sync_scan");
  $("btn-ssync").onclick = () => cmd("sync", [$("in-window").value]);

  $("in-sim").onchange = (e) => cmd("sim_enable", [e.target.checked]);
  $("btn-simset").onclick = () => {
    const [h, m] = ($("in-hhmm").value || "00:00").split(":");
    cmd("sim_time", [Number(h), Number(m)]);
  };
  document.querySelectorAll("[data-nudge]").forEach((b) => {
    b.onclick = () => cmd("sim_nudge", [Number(b.dataset.nudge)]);
  });
  $("btn-simreset").onclick = () => cmd("sim_reset");
  document.querySelectorAll("[data-scen]").forEach((b) => {
    b.onclick = () => cmd("scenario", [b.dataset.scen]);
  });

  $("btn-mic-list").onclick();
}

bind();
connect();
pullLog();
setInterval(pullLog, 4000);
