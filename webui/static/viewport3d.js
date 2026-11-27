/* The "3D viewport" look: the app stands on a grid floor that you can orbit, like a 3D program.
   It is one of the looks of the master switch (cockpit.js: the cube button, or Settings > General > Look)
   and follows <html data-look>. It only adds CSS transforms and a few overlay controls:
   no app state, no native API calls, no network. Styles: viewport3d.css. */
"use strict";
window.viewport3d = (() => {
  const CAMERA_KEY = "aiassist-viewport-camera";
  const SIDE_ANGLE = 22, MIN_ZOOM = 0.4, MAX_ZOOM = 1.3;
  const VIEWS = {
    perspective: {yaw: 14, elevation: 18, zoom: 0.82, label: "User Perspective"},
    front: {yaw: 0, elevation: 0, zoom: 1, label: "Front"},
    top: {yaw: 0, elevation: 90, zoom: 0.8, label: "Top"},
    right: {yaw: -90, elevation: 0, zoom: 0.8, label: "Right"},
    left: {yaw: 90, elevation: 0, zoom: 0.8, label: "Left"},
    back: {yaw: 180, elevation: 0, zoom: 0.9, label: "Rear"},
  };
  const AXES = [
    {axis: "x", sign: 1, view: "right", label: "View from the right (X)"},
    {axis: "x", sign: -1, view: "left", label: "View from the left (−X)"},
    {axis: "y", sign: 1, view: "back", label: "View from the back (Y)"},
    {axis: "y", sign: -1, view: "front", label: "View from the front (−Y)"},
    {axis: "z", sign: 1, view: "top", label: "View from the top (Z)"},
  ];
  const t = (key, vars = {}) => window.i18n ? window.i18n.t(key, vars) : String(key).replace(/\{(\w+)\}/g, (_, name) => String(vars[name] ?? ""));
  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const radians = degrees => degrees * Math.PI / 180;
  const nearestYaw = (target, from) => target + 360 * Math.round((from - target) / 360);
  const store = {
    get(key) { try { return window.localStorage?.getItem(key) ?? null; } catch (_error) { return null; } },
    set(key, value) { try { window.localStorage?.setItem(key, value); } catch (_error) { /* storage unavailable */ } },
  };

  // Where each world axis points on screen for the .shell transform
  // scale3d(zoom) rotateX(-elevation) rotateY(yaw): X is right, Y goes into the screen, Z is up.
  // Each entry is [screen x, screen y (down), depth (towards the viewer)].
  function axes(yaw, elevation) {
    const so = Math.sin(radians(yaw)), co = Math.cos(radians(yaw));
    const se = Math.sin(radians(elevation)), ce = Math.cos(radians(elevation));
    return {x: [co, -so * se, -so * ce], y: [-so, -co * se, -co * ce], z: [0, -ce, se]};
  }
  function transform(camera, pivot = {x: 0, y: 0, z: 0}) {
    const zoom = camera.zoom;
    return "scale3d(" + zoom + "," + zoom + "," + zoom + ") rotateX(" + (-camera.elevation) + "deg) rotateY(" + camera.yaw
      + "deg) translate3d(" + (-pivot.x) + "px," + (-pivot.y) + "px," + (-pivot.z) + "px)";
  }
  function preset(name) {
    const view = VIEWS[name];
    return {view: name, yaw: view.yaw, elevation: view.elevation, zoom: view.zoom, focus: null};
  }
  function readCamera(text) {
    const fallback = {...preset("perspective"), panel: "main", grid: true};
    let saved;
    try { saved = JSON.parse(text); } catch (_error) { return fallback; }
    if (!saved || typeof saved !== "object") return fallback;
    const number = (value, low, high, otherwise) => Number.isFinite(value) ? clamp(value, low, high) : otherwise;
    return {
      view: saved.view === "user" || saved.view === "face" || Object.prototype.hasOwnProperty.call(VIEWS, saved.view) ? saved.view : fallback.view,
      yaw: number(saved.yaw, -100000, 100000, fallback.yaw),
      elevation: number(saved.elevation, 0, 90, fallback.elevation),
      zoom: number(saved.zoom, MIN_ZOOM, MAX_ZOOM, fallback.zoom),
      panel: saved.panel === "sidebar" ? "sidebar" : "main",
      focus: saved.focus === "sidebar" ? "sidebar" : null,
      grid: saved.grid !== false,
    };
  }

  const root = document.documentElement;
  const page = {app: null, shell: null, sidebar: null, main: null};
  const parts = {};
  let look = "classic";
  let camera = readCamera(store.get(CAMERA_KEY));
  let drag = null, pending = false, built = false;

  const active = () => look === "viewport" && built;
  const visible = element => Boolean(element && element.offsetWidth > 0 && element.offsetHeight > 0);
  function svg(paths) {
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.setAttribute("aria-hidden", "true");
    for (const d of paths) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      icon.append(path);
    }
    return icon;
  }
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) { element.textContent = t(text); element.setAttribute("data-i18n", text); }
    return element;
  }
  function button(className, text, onClick, label) {
    const element = node("button", className);
    element.type = "button";
    if (text) { const span = node("span", "", text); element.append(span); }
    if (label) {
      element.setAttribute("aria-label", t(label)); element.setAttribute("data-i18n-aria-label", label);
      element.setAttribute("title", t(label)); element.setAttribute("data-i18n-title", label);
    }
    element.addEventListener("click", onClick);
    return element;
  }

  function pivot() {
    const {shell, sidebar} = page;
    if (camera.focus !== "sidebar" || !visible(sidebar) || !shell) return {x: 0, y: 0, z: 0};
    const width = sidebar.offsetWidth, angle = radians(SIDE_ANGLE);
    const right = sidebar.offsetLeft + width - shell.clientWidth / 2;
    return {
      x: right - width / 2 * Math.cos(angle),
      y: sidebar.offsetTop + sidebar.offsetHeight / 2 - shell.clientHeight / 2,
      z: width / 2 * Math.sin(angle),
    };
  }
  function measure() {
    for (const name of ["sidebar", "main"]) {
      const panel = page[name], slab = parts[name + "Slab"];
      if (!slab) continue;
      slab.hidden = !visible(panel);
      if (slab.hidden) continue;
      slab.style.left = panel.offsetLeft + "px"; slab.style.top = panel.offsetTop + "px";
      slab.style.width = panel.offsetWidth + "px"; slab.style.height = panel.offsetHeight + "px";
    }
  }
  function viewName() {
    if (camera.view === "face") return t(camera.focus === "sidebar" ? "Facing the sidebar" : "Facing the chat");
    return t(VIEWS[camera.view]?.label || "User Perspective");
  }
  function render() {
    if (!built) return;
    root.classList.toggle("vp-grid-off", !camera.grid);
    if (look !== "viewport") {
      page.shell.style.transform = "";
      page.sidebar?.classList.remove("vp-selected"); page.main?.classList.remove("vp-selected");
      return;
    }
    page.shell.style.transform = transform(camera, pivot());
    page.sidebar?.classList.toggle("vp-selected", camera.panel === "sidebar");
    page.main?.classList.toggle("vp-selected", camera.panel === "main");
    const directions = axes(camera.yaw, camera.elevation), center = 46, radius = 31;
    for (const item of parts.axes) {
      const [x, y, depth] = directions[item.axis].map(value => value * item.sign);
      const size = item.sign > 0 ? 24 : 18;
      item.button.style.left = (center + x * radius - size / 2).toFixed(1) + "px";
      item.button.style.top = (center + y * radius - size / 2).toFixed(1) + "px";
      item.button.style.zIndex = String(Math.round((depth + 1) * 10));
      if (item.line) { item.line.setAttribute("x2", (center + x * radius).toFixed(1)); item.line.setAttribute("y2", (center + y * radius).toFixed(1)); }
    }
    for (const [name, element] of Object.entries(parts.views)) element.setAttribute("aria-pressed", String(camera.view === name));
    parts.grid.setAttribute("aria-pressed", String(camera.grid));
    const yaw = Math.round(((camera.yaw + 180) % 360 + 360) % 360 - 180);
    parts.info.textContent = viewName() + " · " + t("{panel} selected", {panel: t(camera.panel === "sidebar" ? "Sidebar" : "Chat")}) + " · "
      + t("Orbit {yaw}° · Elevation {elevation}° · Zoom {zoom}%", {yaw: String(yaw).replace("-", "−"), elevation: Math.round(camera.elevation), zoom: Math.round(camera.zoom * 100)});
  }
  function update(changes, {save = true} = {}) {
    camera = {...camera, ...changes};
    if (save) store.set(CAMERA_KEY, JSON.stringify(camera));
    if (pending) return;
    pending = true;
    (window.requestAnimationFrame || (callback => setTimeout(callback, 16)))(() => { pending = false; render(); });
  }
  function show(name) { update({...preset(name), yaw: nearestYaw(VIEWS[name].yaw, camera.yaw)}); }
  function face() {
    if (camera.panel === "sidebar" && visible(page.sidebar)) update({view: "face", focus: "sidebar", yaw: nearestYaw(-SIDE_ANGLE, camera.yaw), elevation: 0, zoom: 1});
    else update({view: "face", focus: null, yaw: nearestYaw(0, camera.yaw), elevation: 0, zoom: 1});
  }
  function zoomBy(factor) { update({zoom: clamp(Math.round(camera.zoom * factor * 100) / 100, MIN_ZOOM, MAX_ZOOM)}); }
  // The master switch owns the look; this only follows <html data-look>.
  function sync() {
    look = root.getAttribute?.("data-look") === "viewport" ? "viewport" : "classic";
    measure(); render();
  }
  function leave() {
    if (window.lookui?.setLook) window.lookui.setLook("classic");
    else { root.setAttribute("data-look", "classic"); sync(); }
  }

  // Dragging empty space orbits; panels, bars and controls keep their normal behaviour.
  const outside = target => !target?.closest?.(".sidebar, .main, .statusbar, .vp-bar, button, input, select, textarea, a, dialog");
  function pointerDown(event) {
    if (!active() || event.button !== 0 || !outside(event.target)) return;
    event.preventDefault();
    drag = {id: event.pointerId, x: event.clientX, y: event.clientY, yaw: camera.yaw, elevation: camera.elevation};
    try { page.app.setPointerCapture(event.pointerId); } catch (_error) { /* capture is optional */ }
    root.classList.add("vp-dragging");
  }
  function pointerMove(event) {
    if (!drag || event.pointerId !== drag.id) return;
    update({view: "user", yaw: drag.yaw + (event.clientX - drag.x) * 0.25, elevation: clamp(drag.elevation + (event.clientY - drag.y) * 0.2, 0, 90)}, {save: false});
  }
  function pointerUp(event) {
    if (!drag || event.pointerId !== drag.id) return;
    drag = null;
    root.classList.remove("vp-dragging");
    update({});
  }
  function wheel(event) {
    if (!active() || !outside(event.target)) return;
    event.preventDefault();
    update({zoom: clamp(camera.zoom * Math.exp(-event.deltaY * 0.0015), MIN_ZOOM, MAX_ZOOM)});
  }

  function build() {
    page.app = document.querySelector?.(".app");
    page.shell = page.app?.querySelector(".shell");
    if (!page.app || !page.shell) return;
    page.sidebar = page.shell.querySelector(".sidebar");
    page.main = page.shell.querySelector(".main");

    const bar = node("div", "vp-bar vp-only");
    bar.setAttribute("role", "group"); bar.setAttribute("aria-label", t("3D view")); bar.setAttribute("data-i18n-aria-label", "3D view");
    parts.views = {};
    for (const name of ["perspective", "front", "top", "right"]) {
      parts.views[name] = button("btn ghost", name === "perspective" ? "Perspective" : VIEWS[name].label, () => show(name));
      bar.append(parts.views[name]);
    }
    const faceButton = button("btn ghost", "Face selected", face);
    faceButton.prepend(svg(["M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5", "M9 9h6v6H9z"]));
    parts.grid = button("btn ghost", "Grid", () => update({grid: !camera.grid}));
    parts.grid.prepend(svg(["M3 9h18M3 15h18M9 3v18M15 3v18"]));
    parts.info = node("span", "vp-info");
    parts.info.setAttribute("title", t("Drag empty space to orbit, scroll there to zoom, click a panel to select it."));
    parts.info.setAttribute("data-i18n-title", "Drag empty space to orbit, scroll there to zoom, click a panel to select it.");
    bar.append(node("span", "vp-sep"), faceButton, parts.grid, parts.info, button("btn", "Classic look", leave));

    const nav = node("div", "vp-nav vp-only");
    const gizmo = node("div", "vp-gizmo");
    const lines = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    lines.setAttribute("viewBox", "0 0 92 92"); lines.setAttribute("aria-hidden", "true");
    gizmo.append(lines);
    parts.axes = AXES.map(item => {
      const element = button("vp-axis" + (item.sign < 0 ? " vp-negative" : ""), "", () => show(item.view), item.label);
      element.setAttribute("data-axis", item.axis);
      if (item.sign > 0) element.textContent = item.axis.toUpperCase();
      let line = null;
      if (item.sign > 0) {
        line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", "46"); line.setAttribute("y1", "46"); line.setAttribute("data-axis", item.axis);
        lines.append(line);
      }
      gizmo.append(element);
      return {...item, button: element, line};
    });
    const zoom = node("div", "vp-zoom");
    const zoomIn = button("icon-btn", "", () => zoomBy(1.15), "Zoom in");
    zoomIn.append(svg(["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-3.5-3.5M11 8v6M8 11h6"]));
    const zoomOut = button("icon-btn", "", () => zoomBy(1 / 1.15), "Zoom out");
    zoomOut.append(svg(["M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14z", "M20 20l-3.5-3.5M8 11h6"]));
    const home = button("icon-btn", "", () => show("perspective"), "Back to the whole desk");
    home.append(svg(["M3 11l9-7 9 7", "M5 10v10h14V10"]));
    zoom.append(zoomIn, zoomOut, home);
    nav.append(gizmo, zoom);

    const floor = node("div", "vp-floor vp-only");
    floor.setAttribute("aria-hidden", "true");
    const slab = side => {
      const element = node("div", "vp-slab vp-only" + (side ? " vp-slab-side" : ""));
      element.setAttribute("aria-hidden", "true");
      for (const part of ["vp-back", "vp-edge-left", "vp-edge-right", "vp-edge-top", "vp-shadow"]) element.append(node("div", part));
      return element;
    };
    parts.sidebarSlab = slab(true); parts.mainSlab = slab(false);
    page.shell.append(floor, parts.sidebarSlab, parts.mainSlab);
    page.app.prepend(bar);
    page.app.append(nav);

    for (const name of ["sidebar", "main"]) page[name]?.addEventListener("pointerdown", () => { if (active() && camera.panel !== name) update({panel: name}); });
    page.app.addEventListener("pointerdown", pointerDown);
    page.app.addEventListener("pointermove", pointerMove);
    page.app.addEventListener("pointerup", pointerUp);
    page.app.addEventListener("pointercancel", pointerUp);
    page.app.addEventListener("wheel", wheel, {passive: false});
    window.addEventListener?.("resize", () => { measure(); update({}, {save: false}); });
    window.addEventListener?.("languagechange", () => render());
    if (window.MutationObserver) new window.MutationObserver(sync).observe(root, {attributes: true, attributeFilter: ["data-look"]});
    built = true;
    sync();
  }
  build();

  return {
    axes, transform, readCamera, show, face, zoomBy,
    get look() { return look; },
    get camera() { return {...camera}; },
  };
})();
