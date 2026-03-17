# Enterprise C SPC Dashboard - Quick Start Guide

Real-time Statistical Process Control monitoring for SUB-250 bioreactor process.

## What You Have

1. **mcp_web_bridge.py** - Flask server that bridges browser ↔ MCP ↔ AnyLog
2. **enterprise_c_spc_mcp_dashboard.html** - Browser-based SPC dashboard
3. **start_bridge.sh** - Startup script (makes it easy)
4. **MCP_WEB_BRIDGE_SPEC.md** - Complete API documentation

## Installation

### 1. Copy files to your Prove-IT directory

```bash
cd /Users/mdavidson58/Documents/AnyLog/Prove-IT
# Copy all downloaded files here
```

### 2. Install Python dependencies

```bash
source venv/bin/activate
pip install flask flask-cors
```

## Running the Dashboard

### Option A: Using the Startup Script (Recommended)

```bash
cd /Users/mdavidson58/Documents/AnyLog/Prove-IT
./start_bridge.sh
```

The script will:
- ✅ Check all dependencies
- ✅ Activate the virtual environment
- ✅ Install Flask if needed
- ✅ Start the bridge server on port 8080
- ✅ Open dashboard at http://localhost:8080

### Option B: Manual Start

```bash
cd /Users/mdavidson58/Documents/AnyLog/Prove-IT
source venv/bin/activate
python3 mcp_web_bridge.py
```

Then open your browser to: **http://localhost:8080**

## Using the Dashboard

### First Launch

1. Dashboard loads → Status shows "MCP: Checking..."
2. Click **"🔍 Discover UNS"** button
3. Watch the log panel (green terminal at bottom) for:
   ```
   [HH:MM:SS] MCP: checkStatus()
   [HH:MM:SS] MCP: listPolicyTypes()
   [HH:MM:SS] MCP: listTables({dbms: "manufacturing_historian"})
   ```
4. Status should turn green: "MCP: Connected" and "UNS: 29 sensors"
5. Process sections appear with 7 groups:
   - 🌡️ Temperature Control (6 sensors)
   - 🧪 DO & pH (3 sensors)  
   - 💨 Gas Flow Control (6 sensors)
   - 🔧 Flow Control Valves (4 sensors)
   - ⚙️ Agitation & Pumps (7 sensors)
   - 📊 Pressure Control (2 sensors)
   - ⚖️ Weight & Level (1 sensor)

### Controls

- **Baseline Window** - Time range for UCL/LCL calculation (default: 4 hours)
- **Chart Window** - Time range displayed on charts (default: 1 hour)
- **Refresh Interval** - Auto-refresh frequency (default: 30 seconds)
- **↻ Refresh Now** - Force immediate data refresh
- **⏸ Pause** - Stop auto-refresh
- **🗑 Clear Alerts** - Clear all SPC violation alerts

### Reading the Charts

Each chart shows:
- **Blue line** - Actual sensor value over time
- **Red dotted line** - UCL (Upper Control Limit, +3σ)
- **Orange dashed line** - UWL (Upper Warning Limit, +2σ)
- **Green solid line** - Mean (μ)
- **Orange dashed line** - LWL (Lower Warning Limit, -2σ)
- **Blue dotted line** - LCL (Lower Control Limit, -3σ)

**Marker Colors:**
- 🟢 Green - Normal (within ±2σ)
- 🟠 Orange - Warning (between ±2σ and ±3σ)
- 🔴 Red - Violation (beyond ±3σ)

### Alerts Panel

Western Electric Rules detect special cause variation:
- **Rule 1 (Critical)** - Single point beyond ±3σ (UCL/LCL)
- **Rule 2 (High)** - 2 of last 3 points beyond ±2σ same side
- **Rule 3 (Medium)** - 4 of last 5 points beyond ±1σ same side
- **Rule 4 (Medium)** - 8 consecutive points same side of mean

Click any alert to scroll to its chart.

## Troubleshooting

### Dashboard shows "MCP: Offline"

**Cause:** Flask bridge server not running

**Fix:**
```bash
cd /Users/mdavidson58/Documents/AnyLog/Prove-IT
./start_bridge.sh
```

### Bridge starts but "UNS discovery failed: HTTP 503"

**Cause:** MCP proxy can't connect to AnyLog operator

**Check:**
1. Is the AnyLog operator running at 50.116.13.109:32049?
   ```bash
   curl http://50.116.13.109:32049/health
   ```

2. Is mcp_proxy working?
   ```bash
   /Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/bin/mcp_proxy http://50.116.13.109:32049/mcp/sse
   ```

3. Check MCP configuration:
   ```bash
   cat ~/Library/Application\ Support/Claude/claude_desktop_config.json
   ```

### Charts show no data

**Causes:**
1. Table has no recent data in the selected time window
2. Wrong database name
3. Wrong table names

**Debug:**
1. Check table list:
   ```bash
   curl http://localhost:8080/api/tables?dbms=manufacturing_historian
   ```

2. Check data range for a table:
   ```bash
   curl -X POST http://localhost:8080/api/query \
     -H "Content-Type: application/json" \
     -d '{
       "sql": "SELECT min(timestamp) as first, max(timestamp) as last, count(*) as n FROM tic_250_001_pv_celsius"
     }'
   ```

### Port 8080 already in use

**Fix:**
```bash
# Find the process using port 8080
lsof -i :8080

# Kill it
kill -9 <PID>

# Or let start_bridge.sh handle it automatically
```

## System Requirements

- **Python:** 3.8+
- **Browser:** Chrome, Firefox, Safari, or Edge (modern versions)
- **Network:** Access to AnyLog operator at 50.116.13.109:32049
- **Memory:** 200MB for Flask bridge + browser
- **Storage:** 10MB for all files

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser (http://localhost:8080)                        │
│  • enterprise_c_spc_mcp_dashboard.html                  │
│  • Plotly.js charts                                     │
│  • Western Electric Rules engine                        │
└────────────────┬────────────────────────────────────────┘
                 │ HTTP/JSON
                 ▼
┌─────────────────────────────────────────────────────────┐
│  Flask Bridge (localhost:8080)                          │
│  • mcp_web_bridge.py                                    │
│  • 7 REST endpoints                                     │
│  • CORS enabled                                         │
└────────────────┬────────────────────────────────────────┘
                 │ MCP Protocol (subprocess)
                 ▼
┌─────────────────────────────────────────────────────────┐
│  MCP Proxy (venv/bin/mcp_proxy)                         │
│  • Translates HTTP → MCP                                │
│  • SSE connection to AnyLog                             │
└────────────────┬────────────────────────────────────────┘
                 │ SSE/HTTP
                 ▼
┌─────────────────────────────────────────────────────────┐
│  AnyLog Operator (50.116.13.109:32049)                  │
│  • manufacturing_historian database                     │
│  • 86 sensor tables                                     │
│  • Distributed query engine                             │
└─────────────────────────────────────────────────────────┘
```

## Features

✅ Real-time SPC control charts with UCL/LCL  
✅ Western Electric Rules violation detection  
✅ 29 SUB-250 bioreactor sensors  
✅ 7 process groups (Temperature, DO/pH, Gas, Valves, Agitation, Pressure, Weight)  
✅ Auto-refresh with configurable intervals  
✅ Baseline cadence (UCL/LCL every 3rd cycle, trend every cycle)  
✅ Interactive alerts panel with click-to-scroll  
✅ Live activity log (green terminal)  
✅ No authentication required (local development)  

## Data Refresh Strategy

**Baseline Cadence:**
- UCL/LCL recalculated every 3rd refresh cycle (~90s at 30s interval)
- Reduces query load on AnyLog operator by 66%

**Query Pattern per Sensor:**
- **Baseline cycle:** 2 queries (avg/min/max/count + 30-min buckets for σ)
- **Trend cycle:** 1 query (1-min buckets for chart)

**Total Queries per Cycle:**
- Full cycle (baseline + trend): 87 queries for 29 sensors
- Trend-only cycle: 29 queries

**Operator Load:**
- Cycle 1: Full (87 queries)
- Cycle 2: Trend (29 queries)
- Cycle 3: Trend (29 queries)
- Cycle 4: Full (87 queries)
- ...

## File Manifest

```
enterprise_c_spc_mcp_dashboard.html    39 KB   Dashboard UI
mcp_web_bridge.py                       6 KB   Flask bridge server
start_bridge.sh                         2 KB   Startup script
MCP_WEB_BRIDGE_SPEC.md                 48 KB   API documentation
README.md                               8 KB   This file
```

## Support

**Documentation:** See MCP_WEB_BRIDGE_SPEC.md for complete API reference

**Common Issues:**
- MCP offline → Check AnyLog operator status
- No data → Verify table names and time windows
- Port conflict → Kill process on 8080 or use different port

**Contact:** AnyLog Co. - https://github.com/AnyLog-co/documentation

## License

MIT License - Copyright (c) 2026 AnyLog Co.
