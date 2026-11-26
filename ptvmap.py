"""Serve a small, local canvas map for the departure trackers."""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


_PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transport map</title>
<style>
* { box-sizing: border-box; }
html, body { width: 100%; height: 100%; margin: 0; overflow: hidden; }
body { background: #f3f6f8; color: #23313c; font: 14px "Segoe UI", sans-serif; }
header { height: 54px; display: flex; align-items: center; gap: 16px; padding: 0 16px; background: #fff; border-bottom: 1px solid #d5dde3; }
header strong { font-size: 16px; }
header span { color: #697984; }
button { margin-left: auto; border: 1px solid #c5d0d8; background: #fff; border-radius: 5px; padding: 7px 12px; cursor: pointer; color: #23313c; }
main { display: flex; height: calc(100% - 54px); }
#map { width: calc(100% - 300px); height: 100%; cursor: grab; touch-action: none; }
#map.dragging { cursor: grabbing; }
aside { width: 300px; padding: 14px; overflow: auto; background: #fff; border-left: 1px solid #d5dde3; }
h2 { margin: 0 0 10px; font-size: 15px; }
.note { margin: 0 0 14px; color: #697984; line-height: 1.4; }
.departure { padding: 10px 0; border-top: 1px solid #e6ebef; line-height: 1.5; }
.departure-main { display: flex; align-items: flex-start; gap: 7px; }
.departure-destination { flex: 1; font-weight: 600; }
.departure-time { white-space: nowrap; font-variant-numeric: tabular-nums; font-weight: 600; }
.departure-status { margin-top: 3px; font-size: 13px; font-weight: 600; }
.departure-detail { color: #697984; font-size: 12px; }
.route-chip { display: inline-block; margin-right: 7px; padding: 2px 6px; border-radius: 3px; font-size: 12px; }
.legend { color: #697984; font-size: 12px; }
</style>
</head>
<body>
<header><strong id="title">Transport map</strong><span>Estimated positions · not GPS</span><button id="fit">Fit map</button></header>
<main><canvas id="map"></canvas><aside><h2>Upcoming departures</h2><p class="note" id="status">Waiting for tracker data…</p><div id="departures"></div><p class="legend">Vehicle markers are estimated from GTFS times, not GPS · Drag to pan · scroll to zoom</p></aside></main>
<script>
const canvas = document.getElementById("map");
const context = canvas.getContext("2d");
const state = { data: null, transform: null, drag: null, points: [] };
function resize() {
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.max(1, canvas.clientWidth * ratio);
  canvas.height = Math.max(1, canvas.clientHeight * ratio);
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  draw();
}
function projected(point, centerLat) {
  return { x: point.lon * Math.cos(centerLat * Math.PI / 180), y: -point.lat };
}
function allPoints(data) {
  const points = [];
  for (const route of data.routes || []) points.push(...route.stops);
  for (const stop of data.stops || []) points.push(stop);
  for (const vehicle of data.vehicles || []) points.push(vehicle);
  if (data.station && Number.isFinite(data.station.lat) && Number.isFinite(data.station.lon)) points.push(data.station);
  return points.filter(point => Number.isFinite(point.lat) && Number.isFinite(point.lon));
}
function fit() {
  if (!state.data) return;
  const width = canvas.clientWidth, height = canvas.clientHeight;
  const points = allPoints(state.data);
  if (!points.length) { state.transform = null; draw(); return; }
  const centerLat = points.reduce((sum, point) => sum + point.lat, 0) / points.length;
  const projectedPoints = points.map(point => projected(point, centerLat));
  const xs = projectedPoints.map(point => point.x), ys = projectedPoints.map(point => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const spanX = Math.max(maxX - minX, 0.002), spanY = Math.max(maxY - minY, 0.002);
  const scale = Math.min((width - 100) / spanX, (height - 100) / spanY);
  state.transform = {
    scale: Math.max(0.1, Math.min(scale, 500000)),
    centerLat,
    centerX: (minX + maxX) / 2,
    centerY: (minY + maxY) / 2,
    offsetX: width / 2,
    offsetY: height / 2
  };
  draw();
}
function screen(point) {
  const t = state.transform, p = projected(point, t.centerLat);
  return { x: (p.x - t.centerX) * t.scale + t.offsetX, y: (p.y - t.centerY) * t.scale + t.offsetY };
}
function draw() {
  const width = canvas.clientWidth, height = canvas.clientHeight;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#f3f6f8";
  context.fillRect(0, 0, width, height);
  if (!state.data || !state.transform) {
    context.fillStyle = "#697984";
    context.font = "15px Segoe UI, sans-serif";
    context.fillText("No GTFS coordinates available for this stop or its routes.", 24, 40);
    return;
  }
  for (const route of state.data.routes || []) {
    context.beginPath();
    context.strokeStyle = route.color || "#456a8a";
    context.lineWidth = 4;
    context.lineJoin = "round";
    context.lineCap = "round";
    route.stops.forEach((point, index) => {
      const p = screen(point);
      if (index === 0) context.moveTo(p.x, p.y); else context.lineTo(p.x, p.y);
    });
    context.stroke();
  }
  const queriedStopIds = new Set(state.data.station?.stop_ids || []);
  const stationLabelGroups = new Map();
  for (const stop of state.data.stops || []) {
    const p = screen(stop);
    context.beginPath(); context.arc(p.x, p.y, 3.5, 0, Math.PI * 2);
    context.fillStyle = "#fff"; context.fill();
    context.lineWidth = 1.5; context.strokeStyle = "#586a78"; context.stroke();
    if (queriedStopIds.has(stop.id)) continue;
    const key = `${Math.round(p.x / 12)}:${Math.round(p.y / 12)}`;
    const current = stationLabelGroups.get(key);
    if (!current || stop.name.length < current.name.length) {
      stationLabelGroups.set(key, { name: stop.name, x: p.x, y: p.y });
    }
  }
  context.font = "11px Segoe UI, sans-serif";
  context.textBaseline = "middle";
  const occupiedLabels = [];
  for (const label of stationLabelGroups.values()) {
    const text = label.name.toUpperCase();
    const width = context.measureText(text).width;
    const candidates = [];
    for (const offset of [0, -16, 16, -32, 32, -48, 48, -64, 64]) {
      candidates.push(
        [label.x + 7, label.y + offset],
        [label.x - width - 7, label.y + offset],
        [label.x - width / 2, label.y + offset - 12],
        [label.x - width / 2, label.y + offset + 20]
      );
    }
    const placement = candidates.find(([x, y]) => {
      const bounds = { left: x - 2, right: x + width + 2, top: y - 7, bottom: y + 7 };
      return bounds.left >= 0 && bounds.right <= widthOfMap() &&
        bounds.top >= 0 && bounds.bottom <= height &&
        occupiedLabels.every(other =>
          bounds.right + 3 < other.left || bounds.left - 3 > other.right ||
          bounds.bottom + 3 < other.top || bounds.top - 3 > other.bottom
        );
    });
    if (!placement) continue;
    const [x, y] = placement;
    occupiedLabels.push({
      left: x - 2, right: x + width + 2, top: y - 7, bottom: y + 7
    });
    context.fillStyle = "rgba(243, 246, 248, 0.88)";
    context.fillRect(x - 2, y - 8, width + 4, 16);
    context.fillStyle = "#35434d";
    context.fillText(text, x, y);
  }
  context.textBaseline = "alphabetic";
  if (state.data.station && Number.isFinite(state.data.station.lat)) {
    const p = screen(state.data.station);
    context.beginPath(); context.arc(p.x, p.y, 9, 0, Math.PI * 2);
    context.fillStyle = "#e44d4d"; context.fill();
    context.lineWidth = 3; context.strokeStyle = "#fff"; context.stroke();
    context.font = "bold 12px Segoe UI, sans-serif";
    context.fillStyle = "#9e2929";
    context.fillText(state.data.station.name.toUpperCase(), p.x + 12, p.y - 10);
  }
  for (const vehicle of state.data.vehicles || []) {
    const p = screen(vehicle);
    context.beginPath(); context.arc(p.x, p.y, 8, 0, Math.PI * 2);
    context.fillStyle = "#f4a62a"; context.fill();
    context.lineWidth = 2; context.strokeStyle = "#fff"; context.stroke();
    context.font = "bold 11px Segoe UI, sans-serif";
    context.fillStyle = "#684511";
    context.fillText(`To ${vehicle.direction}`, p.x + 10, p.y + 4);
  }
}
function widthOfMap() {
  return canvas.clientWidth;
}
function renderSidebar(data) {
  const title = data.station ? data.station.name : "Transport map";
  document.getElementById("title").textContent = title;
  const updated = data.updated_at ? new Date(data.updated_at).toLocaleTimeString() : "";
  const departures = data.departures || [];
  document.getElementById("status").textContent =
    `${departures.length} departures · Updated ${updated}.`;
  const list = document.getElementById("departures");
  list.replaceChildren();
  for (const departure of departures) {
    const entry = document.createElement("div");
    entry.className = "departure";
    const route = document.createElement("span");
    route.className = "route-chip";
    route.textContent = departure.route;
    route.style.backgroundColor = departure.route_color || "#456a8a";
    route.style.color = departure.route_text_color || "#ffffff";
    const destination = document.createElement("span");
    destination.className = "departure-destination";
    destination.textContent = `To ${departure.destination}`;
    const time = document.createElement("span");
    time.className = "departure-time";
    time.textContent = departure.departure_time;
    const main = document.createElement("div");
    main.className = "departure-main";
    main.append(route, destination, time);
    entry.appendChild(main);
    const status = document.createElement("div");
    status.className = "departure-status";
    status.textContent = departure.status;
    status.style.color = departure.status_color || "#52616b";
    entry.appendChild(status);
    const detail = document.createElement("span");
    detail.className = "departure-detail";
    detail.textContent = [
      departure.platform,
      departure.realtime ? "realtime" : "scheduled",
    ].filter(Boolean).join(" · ");
    entry.appendChild(detail);
    list.appendChild(entry);
  }
  if (!departures.length) {
    const empty = document.createElement("p");
    empty.className = "note";
    empty.textContent = "No upcoming departures.";
    list.appendChild(empty);
  }
}
async function refresh() {
  try {
    const response = await fetch("/data", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.data = data;
    renderSidebar(data);
    if (!state.transform) fit(); else draw();
  } catch (error) {
    document.getElementById("status").textContent = `Tracker map update failed: ${error.message}`;
  }
}
canvas.addEventListener("pointerdown", event => {
  state.drag = { x: event.clientX, y: event.clientY, offsetX: state.transform?.offsetX, offsetY: state.transform?.offsetY };
  canvas.classList.add("dragging"); canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", event => {
  if (!state.drag || !state.transform) return;
  state.transform.offsetX += event.clientX - state.drag.x;
  state.transform.offsetY += event.clientY - state.drag.y;
  state.drag.x = event.clientX; state.drag.y = event.clientY; draw();
});
canvas.addEventListener("pointerup", () => { state.drag = null; canvas.classList.remove("dragging"); });
canvas.addEventListener("wheel", event => {
  if (!state.transform) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left, y = event.clientY - rect.top;
  const oldScale = state.transform.scale;
  const nextScale = Math.max(20, Math.min(oldScale * (event.deltaY < 0 ? 1.2 : 1 / 1.2), 500000));
  const factor = nextScale / oldScale;
  state.transform.offsetX = x - (x - state.transform.offsetX) * factor;
  state.transform.offsetY = y - (y - state.transform.offsetY) * factor;
  state.transform.scale = nextScale; draw();
}, { passive: false });
document.getElementById("fit").addEventListener("click", fit);
window.addEventListener("resize", resize);
resize(); refresh(); setInterval(refresh, 3000);
</script>
</body>
</html>"""


class MapWindow:
	"""Serve a live map to a local browser tab/window."""

	def __init__(self, title):
		self.title = title
		self._data = {}
		self._lock = threading.Lock()
		self._server = None
		self._thread = None

	def start(self):
		if self._server is not None:
			return
		window = self

		class Handler(BaseHTTPRequestHandler):
			def do_GET(self):
				if self.path == "/data":
					with window._lock:
						payload = json.dumps(window._data).encode("utf-8")
					self.send_response(200)
					self.send_header("Content-Type", "application/json; charset=utf-8")
					self.send_header("Cache-Control", "no-store")
					self.send_header("Content-Length", str(len(payload)))
					self.end_headers()
					self.wfile.write(payload)
					return
				if self.path != "/":
					self.send_error(404)
					return
				payload = _PAGE.encode("utf-8")
				self.send_response(200)
				self.send_header("Content-Type", "text/html; charset=utf-8")
				self.send_header("Content-Length", str(len(payload)))
				self.end_headers()
				self.wfile.write(payload)

			def log_message(self, format_string, *args):
				return

		self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
		self._thread = threading.Thread(
			target=self._server.serve_forever,
			name="ptv-map-server",
			daemon=True,
		)
		self._thread.start()
		url = f"http://127.0.0.1:{self._server.server_port}/"
		try:
			opened = webbrowser.open_new(url)
		except Exception as error:
			print(f"Could not open map window automatically: {error}")
			opened = False
		if not opened:
			print(f"Could not open map window automatically: {url}")

	def update(self, data):
		if self._server is None:
			self.start()
		with self._lock:
			self._data = data

	def close(self):
		if self._server is None:
			return
		self._server.shutdown()
		self._server.server_close()
		self._server = None
		self._thread = None
