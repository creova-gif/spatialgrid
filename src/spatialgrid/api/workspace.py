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
      grid-template-columns: 380px 1fr;
      height: 100vh;
      overflow: hidden;
    }
    aside {
      background: var(--panel);
      border-right: 1px solid var(--border);
      padding: 18px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow-y: auto;
    }
    h1 { font-size: 18px; font-weight: 700; letter-spacing: -0.01em; }
    .subtitle { font-size: 12px; color: var(--muted); line-height: 1.4; }
    .section {
      border-top: 1px solid var(--border);
      padding-top: 10px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .row-3 { display: grid; grid-template-columns: 1fr 1fr 72px; gap: 8px; }
    label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 3px; }
    input, textarea, button {
      width: 100%;
      padding: 7px 9px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: #0f172a;
      color: var(--text);
      font-size: 12px;
    }
    textarea { font-family: ui-monospace, monospace; min-height: 68px; resize: vertical; }
    button {
      background: var(--accent);
      color: #052e16;
      font-weight: 600;
      border: none;
      cursor: pointer;
      margin-top: 2px;
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
      max-height: 220px;
    }
    #map { width: 100%; height: 100%; position: relative; }
    @media (max-width: 820px) {
      body { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
      aside { max-height: 48vh; border-right: none; border-bottom: 1px solid var(--border); }
    }
  </style>
</head>
<body>
  <aside>
    <div>
      <h1>SpatialGrid Workspace</h1>
      <p class="subtitle">H3 + STRtree spatial index &amp; OR-Tools CVRP solver</p>
    </div>
    <div class="row">
      <div>
        <label for="orgId">Organization ID</label>
        <input id="orgId" value="demo-org" />
      </div>
      <div>
        <label for="apiToken">Bearer Token</label>
        <input id="apiToken" type="password" placeholder="Optional" />
      </div>
    </div>
    <button id="loadBtn" type="button">Load Facilities</button>
    <div class="section">
      <div class="row-3">
        <div>
          <label for="nearLat">Lat</label>
          <input id="nearLat" type="number" step="0.0001" value="41.8781" />
        </div>
        <div>
          <label for="nearLon">Lon</label>
          <input id="nearLon" type="number" step="0.0001" value="-87.6298" />
        </div>
        <div>
          <label for="nearLimit">Limit</label>
          <input id="nearLimit" type="number" min="1" max="50" value="3" />
        </div>
      </div>
      <button id="nearestBtn" type="button" class="secondary">Nearest Facilities</button>
    </div>
    <div class="section">
      <div>
        <label for="radiusKm">Service Radius (km)</label>
        <input id="radiusKm" type="number" step="0.5" min="0.5" value="10" />
      </div>
      <button id="gapBtn" type="button" class="secondary">Run Coverage Gap</button>
    </div>
    <div class="section">
      <button id="solveBtn" type="button" class="secondary">Run Sample Dispatch (GeoJSON)</button>
    </div>
    <div class="section">
      <label for="ingestInput">GeoJSON FeatureCollection</label>
      <textarea
        id="ingestInput"
        placeholder='{"type":"FeatureCollection","features":[]}'
      ></textarea>
      <button id="ingestBtn" type="button" class="secondary">Ingest GeoJSON</button>
    </div>
    <div class="section">
      <label>Status &amp; Metrics</label>
      <pre id="output">Ready. Click "Load Facilities" to inspect indexed depots.</pre>
    </div>
  </aside>
  <main id="map"></main>
  <script>
    const outputEl = document.getElementById("output");
    const orgInput = document.getElementById("orgId");
    const tokenInput = document.getElementById("apiToken");
    const styleUrl = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json";

    function getOrgId() {
      return orgInput.value.trim() || "demo-org";
    }

    function authHeaders() {
      const headers = { "Content-Type": "application/json" };
      const tok = tokenInput.value.trim();
      if (tok) headers["Authorization"] = "Bearer " + tok;
      return headers;
    }

    function sampleDemands(orgId) {
      return [
        {
          demand_id: "d-near",
          org_id: orgId,
          location: { lat: 41.885, lon: -87.635 },
          demand_units: 12
        },
        {
          demand_id: "d-west",
          org_id: orgId,
          location: { lat: 41.89, lon: -87.72 },
          demand_units: 14
        },
        {
          demand_id: "d-far",
          org_id: orgId,
          location: { lat: 42.12, lon: -87.98 },
          demand_units: 18
        }
      ];
    }

    let deckgl = null;
    if (window.deck && window.deck.DeckGL) {
      deckgl = new window.deck.DeckGL({
        container: "map",
        mapStyle: styleUrl,
        initialViewState: { longitude: -87.6298, latitude: 41.8781, zoom: 10.5 },
        controller: true,
        layers: []
      });
    }

    function renderLayers(opts) {
      if (!deckgl || !window.deck) return;
      const { facilities, routeGeojson, uncoveredNodes, nearestMatches } = opts || {};
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
      if (nearestMatches && nearestMatches.length) {
        layers.push(new window.deck.ScatterplotLayer({
          id: "nearest-matches",
          data: nearestMatches,
          getPosition: (d) => [d.facility.location.lon, d.facility.location.lat],
          getFillColor: [56, 189, 248, 230],
          getRadius: 320,
          pickable: true
        }));
      }
      if (uncoveredNodes && uncoveredNodes.length) {
        layers.push(new window.deck.ScatterplotLayer({
          id: "uncovered-demand",
          data: uncoveredNodes,
          getPosition: (d) => [d.demand_node.location.lon, d.demand_node.location.lat],
          getFillColor: [239, 68, 68, 230],
          getRadius: 280,
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
    async function loadFacilities() {
      const orgId = encodeURIComponent(getOrgId());
      const res = await fetch("/v1/facilities?org_id=" + orgId, { headers: authHeaders() });
      const data = await res.json();
      cachedFacilities = data.facilities || [];
      outputEl.textContent = JSON.stringify(
        { org_id: data.org_id, count: cachedFacilities.length },
        null,
        2
      );
      renderLayers({ facilities: cachedFacilities });
    }

    document.getElementById("loadBtn").addEventListener("click", loadFacilities);

    document.getElementById("nearestBtn").addEventListener("click", async () => {
      const orgId = getOrgId();
      const lat = parseFloat(document.getElementById("nearLat").value);
      const lon = parseFloat(document.getElementById("nearLon").value);
      const limit = parseInt(document.getElementById("nearLimit").value, 10) || 3;
      const res = await fetch("/v1/facilities/nearest", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ org_id: orgId, origin: { lat, lon }, limit })
      });
      const data = await res.json();
      outputEl.textContent = JSON.stringify(data, null, 2);
      renderLayers({ facilities: cachedFacilities, nearestMatches: data.matches || [] });
    });

    document.getElementById("gapBtn").addEventListener("click", async () => {
      const orgId = getOrgId();
      const radius = parseFloat(document.getElementById("radiusKm").value) || 10.0;
      const res = await fetch("/v1/coverage-gap", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({
          org_id: orgId,
          demand_nodes: sampleDemands(orgId),
          service_radius_km: radius
        })
      });
      const data = await res.json();
      outputEl.textContent = JSON.stringify(data, null, 2);
      renderLayers({
        facilities: cachedFacilities,
        uncoveredNodes: data.uncovered_nodes || []
      });
    });

    document.getElementById("solveBtn").addEventListener("click", async () => {
      const orgId = getOrgId();
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
          vehicles: [
            { vehicle_id: "veh-1", capacity: 30 },
            { vehicle_id: "veh-2", capacity: 30 }
          ],
          stops: sampleDemands(orgId)
        })
      });
      const geojson = await res.json();
      outputEl.textContent = JSON.stringify(geojson, null, 2);
      renderLayers({ facilities: cachedFacilities, routeGeojson: geojson });
    });

    document.getElementById("ingestBtn").addEventListener("click", async () => {
      const orgId = getOrgId();
      const raw = document.getElementById("ingestInput").value.trim();
      if (!raw) {
        outputEl.textContent = "Paste a GeoJSON FeatureCollection first.";
        return;
      }
      let geojson;
      try {
        geojson = JSON.parse(raw);
      } catch (err) {
        outputEl.textContent = "Invalid JSON: " + err.message;
        return;
      }
      const res = await fetch("/v1/facilities/ingest", {
        method: "POST",
        headers: authHeaders(),
        body: JSON.stringify({ org_id: orgId, geojson })
      });
      const data = await res.json();
      outputEl.textContent = JSON.stringify(data, null, 2);
      if (res.ok) await loadFacilities();
    });
  </script>
</body>
</html>
"""
