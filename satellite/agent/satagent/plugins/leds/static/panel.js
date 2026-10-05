"use strict";
// LED ring panel for the satellite web UI: live preview, settings, tests.
window.SatPlugins = window.SatPlugins || {};
window.SatPlugins.leds = {
  async mount(el, ctx) {
    const cat = await ctx.api("catalog");
    const s = await ctx.settings();
    el.innerHTML = `
      <style>
        .led-wrap { display:grid; grid-template-columns: minmax(200px, 260px) 1fr; gap:24px; align-items:start; }
        @media (max-width: 640px) { .led-wrap { grid-template-columns: 1fr; } }
        .ring { position:relative; width:100%; aspect-ratio:1; border-radius:50%; background: radial-gradient(circle, #0b0d12 55%, #12151c 56%, #0b0d12 70%); }
        .ring i { position:absolute; width:12%; height:12%; border-radius:50%; transform:translate(-50%,-50%); background:#000; transition: background .03s linear, box-shadow .03s linear; }
        .ring b { position:absolute; inset:0; display:grid; place-items:center; color:#8b93a1; font-size:12px; font-weight:500; }
        .led-tests { display:flex; flex-wrap:wrap; gap:6px; }
        .pal { display:inline-flex; gap:2px; vertical-align:middle; margin-left:6px; }
        .pal span { width:14px; height:14px; border-radius:4px; }
      </style>
      <div class="led-wrap">
        <div><div class="ring" id="led-ring"><b id="led-base"></b></div></div>
        <div>
          <label class="field"><span>Brightness <b id="led-bv"></b></span><input type="range" id="led-br" min="0" max="100" step="1"></label>
          <label class="field"><span>Palette <span class="pal" id="led-pal"></span></span>
            <select id="led-palette">${Object.keys(cat.palettes).map(p => `<option>${p}</option>`).join("")}</select></label>
          <label class="field"><span>When idle</span><select id="led-idle"><option value="off">off</option><option value="ambient">soft ambient glow</option></select></label>
          <label class="switch"><input type="checkbox" id="led-offline"><span>Show when the server is unreachable</span></label>
          <label class="field"><span>Orientation: LED facing 0° <b id="led-ov"></b></span><input type="range" id="led-offset" min="0" max="11" step="1"></label>
          <label class="switch"><input type="checkbox" id="led-reverse"><span>LEDs numbered counter-clockwise</span></label>
          <p class="hint">To align: start "listening", talk from one side, and move the offset until the light faces you.</p>
          <h3>Try the animations</h3>
          <div class="row"><button class="primary" id="led-demo">Play a full interaction</button></div>
          <div class="led-tests">${cat.bases.map(b => `<button data-t="${b}">${b}</button>`).join("")}</div>
          <div class="led-tests" style="margin-top:6px">${cat.overlays.map(b => `<button data-t="${b}">${b}</button>`).join("")}</div>
        </div>
      </div>`;
    const $ = q => el.querySelector(q);
    const ring = $("#led-ring");
    const dots = [];
    for (let i = 0; i < 12; i++) {
      const d = document.createElement("i"), a = i / 12 * 2 * Math.PI;
      d.style.left = 50 + 41 * Math.sin(a) + "%"; d.style.top = 50 - 41 * Math.cos(a) + "%";
      ring.append(d); dots.push(d);
    }
    const showPal = () => { $("#led-pal").innerHTML = cat.palettes[$("#led-palette").value].map(c => `<span style="background:${c}"></span>`).join(""); };
    const fill = st => {
      $("#led-br").value = st.brightness; $("#led-bv").textContent = st.brightness + " %";
      $("#led-palette").value = st.palette; $("#led-idle").value = st.idle;
      $("#led-offline").checked = st.show_offline; $("#led-offset").value = st.offset; $("#led-ov").textContent = "LED " + st.offset;
      $("#led-reverse").checked = st.reverse; showPal();
    };
    fill(s);
    let h;
    const save = data => { clearTimeout(h); h = setTimeout(() => ctx.save(data).then(fill), 150); };
    $("#led-br").oninput = e => { $("#led-bv").textContent = e.target.value + " %"; save({ brightness: +e.target.value }); };
    $("#led-palette").onchange = e => { showPal(); save({ palette: e.target.value }); };
    $("#led-idle").onchange = e => save({ idle: e.target.value });
    $("#led-offline").onchange = e => save({ show_offline: e.target.checked });
    $("#led-offset").oninput = e => { $("#led-ov").textContent = "LED " + e.target.value; save({ offset: +e.target.value }); };
    $("#led-reverse").onchange = e => save({ reverse: e.target.checked });
    $("#led-demo").onclick = () => ctx.api("demo", {}).then(() => ctx.toast("Demo: boot, wake, listening, thinking, speaking, volume, mute, alarm…"));
    el.querySelectorAll("[data-t]").forEach(b => b.onclick = () => ctx.api("test", { name: b.dataset.t, seconds: 6 }));

    // live preview (~25 fps while visible)
    const tick = async () => {
      if (el.offsetParent !== null) {
        try {
          const p = await ctx.api("preview");
          p.frame.forEach((c, i) => {
            dots[i].style.background = c;
            dots[i].style.boxShadow = c === "#000000" ? "none" : `0 0 14px 3px ${c}`;
          });
          $("#led-base").textContent = p.base;
        } catch (e) { }
      }
      setTimeout(tick, el.offsetParent !== null ? 40 : 500);
    };
    tick();
  },
};
