"""Interactive Deck.gl workspace HTML page for SpatialGrid."""

from __future__ import annotations

WORKSPACE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>SpatialGrid — Neuro-Symbolic Dispatch Workspace</title>
  <script src="https://unpkg.com/deck.gl@9.0.16/dist.min.js"></script>
  <script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
  <link href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css" rel="stylesheet" />
  <style>
    :root {
      --bg: #0b1120;
      --panel: #111827;
      --border: #1f2937;
      --text: #f8fafc;
      --muted: #94a3b8;
      --accent: #10b981;
      --warn: #f59e0b;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      display: grid;
      grid-template-columns: 360px 1fr;
      height: 100vh;
      overflow: hidden;
    }
    aside {
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 20px;
      display: flex;
      flex-direction: column;
      gap: 14px;
      overflow-y: auto;
    }
    h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.01em; }
    .subtitle { font-size: 12px; color: var(--muted); line-height: 1.4; }
    label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
    input, button {
      width: 100%;
      padding: 8px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      font-size: 13px;
    }
    button {
      background: var(--accent);
      color: #052e16;
      font-weight: 600;
      border: none;
      cursor: pointer;
      margin-top: 4px;
    }
    button:hover { opacity: 0.92; }
    .secondary { background: #1e293b; color: var(--text); border: 1px solid var(--border); }
    pre {
      background: #020617;
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      font-size: 11px;
      color: #cbd5e1;
      overflow-x: auto;
      max-height: 240px;
    }
    #map { width: 100%; height: 100%; position: relative; }
    @media (max-width: 820px) {
      body { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
      aside { max-height: 45vh; border-right: none; border-bottom: 1px solid var(--border); }
    }
  </style>
</head>
<body>
  <aside>
    <div>
      <h1>SpatialGrid Workspace</h1>
      <p class="subtitle">H3 + STRtree spatial index &amp; OR-Tools CVRP solver</p>
    </div>
    <div>
      <label for="orgId">Organization ID</label>
      <input id="orgId" value="demo-org" />
    </div>
    <div>
      <label for="apiToken">Bearer Token (optional)</label>
      <input id="apiToken" type="password" placeholder="Only if --require-auth is enabled" />
    </div>
    <button id="loadBtn" type="button">Load Facilities</button>
    <button id="solveBtn" type="button" class="secondary">Run Sample Dispatch (GeoJSON)</button>
    <div>
      <label>Status &amp; Metrics</label>
      <pre id="output">Ready. Click "Load Facilities" to inspect indexed depots.</pre>
    </div>
  </aside>
  <main id="map"></main>
  <script>
    const outputEl = document.getElementById("output");
    const orgInput = document.getElementById("orgId");
    const tokenInput = document.getElementById("apiToken");

    function authHeaders() {
      const headers = { "Content-Type": "application/json" };
      const tok = tokenInput.value.trim();
      if (tok) headers["Authorization"] = "Bearer " + tok;
      return headers;
    }

    let deckgl = null;
    if (window.deck && window.deck.DeckGL) {
      deckgl = new window.deck.DeckGL({
        container: "map",
        mapStyle: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
        initialViewState: { longitude: -87.6298, latitude: 41.8781, zoom: 10.5 },
        controller: true,
        layers: []
      });
    }

    function renderLayers(facilities, routeGeojson) {
      if (!deckgl || !window.deck) return;
      const layers = [];
      if (facilities && facilities.length) {
        layers.push(new window.deck.ScatterplotLayer({
          id: "facilities",
          data: facilities,
          getPosition: (d) => [d.location.lon, d.location.lat],
          getFillColor: [16, 185, 129, 210],
          getRadius: 220,
          pickable: true
        }));
      }
      if (routeGeojson && routeGeojson.features) {
        layers.push(new window.deck.GeoJsonLayer({
          id: "dispatch-routes",
          data: routeGeojson,
          stroked: true,
          filled: true,
          getLineColor: [56, 189, 248, 230],
          getFillColor: [245, 158, 11, 220],
          getLineWidth: 4,
          lineWidthMinPixels: 2,
          pointRadiusMinPixels: 5
        }));
      }
      deckgl.setProps({ layers });
    }

    let cachedFacilities = [];
    document.getElementById("loadBtn").addEventListener("click", async () => {
      const orgId = encodeURIComponent(orgInput.value.trim() || "demo-org");
      const res = await fetch("/v1/facilities?org_id=" + orgId, { headers: authHeaders() });
      const data = await res.json();
      cachedFacilities = data.facilities || [];
      outputEl.textContent = JSON.stringify(
        { org_id: data.org_id, count: cachedFacilities.length },
        null,
        2
      );
      renderLayers(cachedFacilities, null);
    });

    document.getElementById("solveBtn").addEventListener("click", async () => {
      const orgId = orgInput.value.trim() || "demo-org";
      if (!cachedFacilities.length) {
        outputEl.textContent = "Load facilities first.";
        return;
      }
      const depotId = cachedFacilities[0].facility_id;
      const res = await fetch("/v1/dispatch/optimize?format=geojson", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          org_id: orgId,
          depot_facility_id: depotId,
          vehicles: [{ vehicle_id: "veh-1", capacity: 30 }, { vehicle_id: "veh-2", capacity: 30 }],
          stops: [
            {
              demand_id: "s1",
              org_id: orgId,
              location: { lat: 41.89, lon: -87.64 },
              demand_units: 12
            },
            {
              demand_id: "s2",
              org_id: orgId,
              location: { lat: 41.86, lon: -87.65 },
              demand_units: 15
            },
            {
              demand_id: "s3",
              org_id: orgId,
              location: { lat: 41.91, lon: -87.68 },
              demand_units: 14
            }
          ]
        })
      });
      const geojson = await res.json();
      outputEl.textContent = JSON.stringify(geojson, null, 2);
      renderLayers(cachedFacilities, geojson);
    });
  </script>
</body>
</html>
"""
