# MCP Web Bridge Specification

**Version:** 1.0  
**Date:** 2026-02-17  
**Purpose:** HTTP/JSON bridge between web browsers and AnyLog MCP (Model Context Protocol) servers

---

## Overview

The MCP Web Bridge is a Flask-based HTTP server that translates browser HTTP requests into MCP protocol calls, enabling web dashboards to query AnyLog distributed databases without direct MCP client implementation.

**Architecture:**
```
Browser/Dashboard (JavaScript)
    ↓ HTTP/JSON (localhost:8080)
Flask Bridge Server (Python)
    ↓ MCP Protocol (subprocess)
mcp_proxy binary (venv/bin/mcp_proxy)
    ↓ SSE/HTTP
AnyLog Operator Node (50.116.13.109:32049)
    ↓
manufacturing_historian database (86 tables)
```

---

## Server Configuration

### Default Settings
- **Host:** `0.0.0.0` (all interfaces)
- **Port:** `8080`
- **CORS:** Enabled (allows file:// and http:// origins)
- **MCP Proxy Path:** `/Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/bin/mcp_proxy`
- **MCP Server URL:** `http://50.116.13.109:32049/mcp/sse`

### Installation
```bash
cd /Users/mdavidson58/Documents/AnyLog/Prove-IT
source venv/bin/activate
pip install flask flask-cors
python3 mcp_web_bridge.py
```

---

## API Endpoints

### 1. Health Check

**GET** `/api/status`

Check MCP connectivity and server health.

**Response (200 OK):**
```json
{
  "status": "online",
  "mcp": {
    "version": "1.0",
    "connected": true
  }
}
```

**Response (503 Service Unavailable):**
```json
{
  "status": "offline",
  "error": "MCP connector offline - awaiting anylog-proveit connectivity"
}
```

---

### 2. UNS Discovery

**GET** `/api/uns/discover`

Discover Unified Namespace (UNS) structure for Enterprise C.

**Response (200 OK):**
```json
{
  "policyTypes": ["uns", "tag", "namespace", "operator"],
  "unsPolicies": [
    {
      "uns": {
        "id": "enterprise_c_uns",
        "name": "enterprise c",
        "children": ["sub", "sum", "tff", "chrom"]
      }
    }
  ],
  "enterpriseC": [
    {"namespace": {"id": "sub", "name": "sub"}},
    {"namespace": {"id": "sum", "name": "sum"}},
    {"namespace": {"id": "tff", "name": "tff"}},
    {"namespace": {"id": "chrom", "name": "chrom"}}
  ],
  "sub": [
    {"tag": {"name": "tic_250_001", "table": "tic_250_001_pv_celsius"}}
  ]
}
```

**MCP Calls Made:**
1. `anylog-proveit:listPolicyTypes`
2. `anylog-proveit:listPolicies{policyType: "uns"}`
3. `anylog-proveit:getPolicyChildren{whereCond: "name = enterprise c"}`
4. `anylog-proveit:getPolicyChildren{whereCond: "name = sub"}`

---

### 3. List Tables

**GET** `/api/tables?dbms={database_name}`

List all tables in a specified database.

**Parameters:**
- `dbms` (optional, default: `manufacturing_historian`) - Database name

**Example Request:**
```
GET /api/tables?dbms=manufacturing_historian
```

**Response (200 OK):**
```json
[
  "tic_250_001_pv_celsius",
  "tic_250_001_sp_celsius",
  "tic_250_002_pv_celsius",
  "fic_250_001_pv_slpm",
  "sic_250_006_pv_ml_per_min",
  "pic_250_001_pv_psi",
  "wi_250_001_pv_kg"
]
```

**MCP Call:**
- `anylog-proveit:listTables{dbms: "manufacturing_historian"}`

---

### 4. List Columns

**GET** `/api/columns?dbms={database}&table={table_name}`

Get column definitions for a specific table.

**Parameters:**
- `dbms` (optional, default: `manufacturing_historian`) - Database name
- `table` (required) - Table name

**Example Request:**
```
GET /api/columns?dbms=manufacturing_historian&table=tic_250_001_pv_celsius
```

**Response (200 OK):**
```json
{
  "columns": [
    {"name": "timestamp", "type": "TIMESTAMP"},
    {"name": "value", "type": "FLOAT"}
  ]
}
```

**Response (400 Bad Request):**
```json
{
  "error": "Missing table parameter"
}
```

**MCP Call:**
- `anylog-proveit:listColumns{dbms: "manufacturing_historian", table: "tic_250_001_pv_celsius"}`

---

### 5. Execute SQL Query

**POST** `/api/query`

Execute a SQL query on the AnyLog network.

**Request Body:**
```json
{
  "dbms": "manufacturing_historian",
  "sql": "SELECT avg(value) as mean, min(value) as minv, max(value) as maxv, count(*) as n FROM tic_250_001_pv_celsius WHERE timestamp >= NOW() - 4 hours",
  "nodes": "172.105.60.50:32148"  // optional - target specific nodes
}
```

**Required Fields:**
- `sql` - SQL query string (must include FROM clause)

**Optional Fields:**
- `dbms` (default: `manufacturing_historian`)
- `nodes` - Comma-separated node addresses to target specific operators

**Response (200 OK):**
```json
[
  {
    "mean": 37.245,
    "minv": 36.8,
    "maxv": 37.9,
    "n": 14400
  }
]
```

**Response (400 Bad Request):**
```json
{
  "error": "Missing SQL"
}
```

**MCP Call:**
- `anylog-proveit:executeQuery{dbms: "manufacturing_historian", sql: "SELECT ...", nodes: "..."}`

**Notes:**
- SQL must be valid AnyLog SQL (subset of ANSI SQL)
- Nested queries and JOINs are not supported
- Use `NOW() - N hours/days/minutes` format (not PostgreSQL INTERVAL)

---

### 6. Execute Incremental Query

**POST** `/api/query/increment`

Execute time-bucketed aggregation query (for trend charts).

**Request Body:**
```json
{
  "dbms": "manufacturing_historian",
  "table": "tic_250_001_pv_celsius",
  "timeColumn": "timestamp",
  "startTime": "2026-02-17 15:00:00",
  "endTime": "2026-02-17 16:00:00",
  "timeUnit": "minute",
  "intervalLength": 1,
  "projections": ["avg(value) as value"]
}
```

**Required Fields:**
- `dbms` - Database name
- `table` - Table name
- `timeColumn` - Name of timestamp column
- `startTime` - Query start time (ISO 8601 or `YYYY-MM-DD HH:MM:SS`)
- `endTime` - Query end time (ISO 8601 or `YYYY-MM-DD HH:MM:SS`)
- `timeUnit` - Time bucket unit: `minute`, `hour`, `day`, `week`, `month`, `year`
- `intervalLength` - Number of time units per bucket (e.g., `5` for 5-minute buckets)
- `projections` - Array of aggregate expressions (e.g., `["avg(value) as value", "count(*) as n"]`)

**Response (200 OK):**
```json
[
  {
    "timestamp": "2026-02-17 15:00:00",
    "value": 37.2
  },
  {
    "timestamp": "2026-02-17 15:01:00",
    "value": 37.3
  },
  {
    "timestamp": "2026-02-17 15:02:00",
    "value": 37.1
  }
]
```

**Response (400 Bad Request):**
```json
{
  "error": "Missing required field: timeColumn"
}
```

**MCP Call:**
- `anylog-proveit:queryWithIncrement{dbms, table, timeColumn, startTime, endTime, timeUnit, intervalLength, projections}`

**Notes:**
- This endpoint replaces the need to manually construct `SELECT increments(...)` SQL
- The MCP proxy automatically generates the AnyLog increments query
- Returns one row per time bucket

---

### 7. Dashboard Home

**GET** `/`

Serve the dashboard HTML file (if present in same directory).

**Response (200 OK):**
- HTML content of `enterprise_c_spc_mcp_dashboard.html`

**Response (200 OK - No Dashboard):**
- Server info page with API documentation links

---

## Error Handling

All endpoints follow consistent error response format:

**Error Response Structure:**
```json
{
  "error": "Human-readable error message"
}
```

**HTTP Status Codes:**
- `200` - Success
- `400` - Bad Request (missing/invalid parameters)
- `500` - Internal Server Error (MCP call failed)
- `503` - Service Unavailable (MCP offline)

---

## Client Usage Examples

### JavaScript (Browser)

```javascript
// Health check
const status = await fetch('http://localhost:8080/api/status');
const data = await status.json();
console.log(data.status);  // "online" or "offline"

// List tables
const tables = await fetch('http://localhost:8080/api/tables?dbms=manufacturing_historian');
const tableList = await tables.json();
console.log(tableList);  // ["tic_250_001_pv_celsius", ...]

// Execute query
const response = await fetch('http://localhost:8080/api/query', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    dbms: 'manufacturing_historian',
    sql: 'SELECT avg(value) as mean FROM tic_250_001_pv_celsius WHERE timestamp >= NOW() - 1 hour'
  })
});
const rows = await response.json();
console.log(rows[0].mean);  // 37.245

// Incremental query (trend data)
const trend = await fetch('http://localhost:8080/api/query/increment', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    dbms: 'manufacturing_historian',
    table: 'tic_250_001_pv_celsius',
    timeColumn: 'timestamp',
    startTime: '2026-02-17 15:00:00',
    endTime: '2026-02-17 16:00:00',
    timeUnit: 'minute',
    intervalLength: 5,
    projections: ['avg(value) as value', 'count(*) as n']
  })
});
const buckets = await trend.json();
console.log(buckets);  // [{timestamp: "...", value: 37.2, n: 300}, ...]
```

### Python

```python
import requests

# Health check
r = requests.get('http://localhost:8080/api/status')
print(r.json()['status'])

# Execute query
r = requests.post('http://localhost:8080/api/query', json={
    'dbms': 'manufacturing_historian',
    'sql': 'SELECT count(*) as n FROM tic_250_001_pv_celsius WHERE timestamp >= NOW() - 1 day'
})
print(r.json()[0]['n'])

# Incremental query
r = requests.post('http://localhost:8080/api/query/increment', json={
    'dbms': 'manufacturing_historian',
    'table': 'tic_250_001_pv_celsius',
    'timeColumn': 'timestamp',
    'startTime': '2026-02-17 00:00:00',
    'endTime': '2026-02-17 23:59:59',
    'timeUnit': 'hour',
    'intervalLength': 1,
    'projections': ['avg(value) as value']
})
for row in r.json():
    print(f"{row['timestamp']}: {row['value']}")
```

### cURL

```bash
# Health check
curl http://localhost:8080/api/status

# List tables
curl "http://localhost:8080/api/tables?dbms=manufacturing_historian"

# Execute query
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "dbms": "manufacturing_historian",
    "sql": "SELECT avg(value) FROM tic_250_001_pv_celsius WHERE timestamp >= NOW() - 1 hour"
  }'

# Incremental query
curl -X POST http://localhost:8080/api/query/increment \
  -H "Content-Type: application/json" \
  -d '{
    "dbms": "manufacturing_historian",
    "table": "tic_250_001_pv_celsius",
    "timeColumn": "timestamp",
    "startTime": "2026-02-17 15:00:00",
    "endTime": "2026-02-17 16:00:00",
    "timeUnit": "minute",
    "intervalLength": 1,
    "projections": ["avg(value) as value"]
  }'
```

---

## Database Schema

### manufacturing_historian

**Table Naming Convention:**
`{device_type}_{device_id}_{measurement}_{unit}`

**Examples:**
- `tic_250_001_pv_celsius` - Temperature Indicating Controller 250-001, Process Value, Celsius
- `fic_250_002_sp_slpm` - Flow Indicating Controller 250-002, Setpoint, Standard Liters Per Minute
- `sic_250_006_pv_rpm` - Speed Indicating Controller 250-006, Process Value, RPM
- `pic_250_001_pv_psi` - Pressure Indicating Controller 250-001, Process Value, PSI

**Device Types:**
- `TIC` - Temperature Indicating Controller
- `TI` - Temperature Indicator
- `FIC` - Flow Indicating Controller
- `FCV` - Flow Control Valve
- `SIC` - Speed Indicating Controller
- `AIC` - Analyzer Indicating Controller (DO, pH)
- `PIC` - Pressure Indicating Controller
- `WI` - Weight Indicator

**Measurement Types:**
- `PV` - Process Value (actual reading)
- `SP` - Setpoint (target value)

**Standard Schema:**
```sql
CREATE TABLE {table_name} (
  timestamp TIMESTAMP,
  value FLOAT
);
```

**Total Tables:** 86 sensor tables

---

## Process Groups (Enterprise C SUB-250)

### 1. Temperature Control (🌡️)
- `tic_250_001_pv_celsius` - TIC-250-001 Process Value
- `tic_250_001_sp_celsius` - TIC-250-001 Setpoint
- `tic_250_002_pv_celsius` - TIC-250-002 Process Value
- `tic_250_002_sp_celsius` - TIC-250-002 Setpoint
- `ti_250_001_pv_celsius` - TI-250-001 Process Value
- `ti_250_002_pv_celsius` - TI-250-002 Process Value

### 2. Dissolved Oxygen & pH (🧪)
- `aic_250_001_pv_percent` - AIC-250-001 DO Process Value (%)
- `aic_250_001_sp_percent` - AIC-250-001 DO Setpoint (%)
- `aic_250_003_sp_ph` - AIC-250-003 pH Setpoint

### 3. Gas Flow Control (💨)
- `fic_250_001_pv_slpm` - FIC-250-001 Air Process Value (sLpm)
- `fic_250_001_sp_slpm` - FIC-250-001 Air Setpoint (sLpm)
- `fic_250_002_pv_slpm` - FIC-250-002 O₂ Process Value (sLpm)
- `fic_250_002_sp_slpm` - FIC-250-002 O₂ Setpoint (sLpm)
- `fic_250_003_pv_slpm` - FIC-250-003 CO₂ Process Value (sLpm)
- `fic_250_003_sp_slpm` - FIC-250-003 CO₂ Setpoint (sLpm)

### 4. Flow Control Valves (🔧)
- `fcv_250_001_pv_percent` - FCV-250-001 Process Value (%)
- `fcv_250_001_sp_percent` - FCV-250-001 Setpoint (%)
- `fcv_250_002_sp_percent` - FCV-250-002 Setpoint (%)
- `fcv_250_003_sp_percent` - FCV-250-003 Setpoint (%)

### 5. Agitation & Pump Control (⚙️)
- `sic_250_002_pv_rpm` - SIC-250-002 Process Value (RPM)
- `sic_250_006_pv_ml_per_min` - SIC-250-006 Process Value (mL/min)
- `sic_250_006_sp_ml_per_min` - SIC-250-006 Setpoint (mL/min)
- `sic_250_008_pv_rpm` - SIC-250-008 Process Value (RPM)
- `sic_250_008_sp_rpm` - SIC-250-008 Setpoint (RPM)
- `sic_250_media_sp_l_per_min` - SIC-250-MEDIA Setpoint (L/min)

### 6. Pressure Control (📊)
- `pic_250_001_pv_psi` - PIC-250-001 Process Value (PSI)
- `pic_250_001_sp_psi` - PIC-250-001 Setpoint (PSI)

### 7. Weight & Level (⚖️)
- `wi_250_001_pv_kg` - WI-250-001 Process Value (kg)

**Total Sensors:** 29 analog sensors

---

## MCP Protocol Details

### Internal MCP Call Structure

When the bridge receives an HTTP request, it constructs an MCP request:

```python
mcp_request = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
        "name": "anylog-proveit:executeQuery",
        "arguments": {
            "dbms": "manufacturing_historian",
            "sql": "SELECT avg(value) FROM tic_250_001_pv_celsius WHERE timestamp >= NOW() - 1 hour"
        }
    }
}
```

The request is piped to `mcp_proxy` via subprocess:

```python
result = subprocess.run(
    [MCP_PROXY_PATH, MCP_SERVER_URL],
    input=json.dumps(mcp_request).encode(),
    capture_output=True,
    timeout=30
)
```

The MCP proxy returns:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": [
    {"avg(value)": 37.245}
  ]
}
```

---

## Troubleshooting

### Bridge Won't Start

**Error:** `ModuleNotFoundError: No module named 'flask'`
```bash
pip install flask flask-cors
```

**Error:** `PermissionError: [Errno 1] Operation not permitted`
```bash
chmod -R u+rw /Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/
```

### MCP Connection Fails

**Error:** `HTTP 503` from `/api/status`

**Causes:**
1. MCP proxy path incorrect
2. AnyLog operator node offline
3. Port/URL misconfigured

**Debug:**
```bash
# Test MCP proxy manually
/Users/mdavidson58/Documents/AnyLog/Prove-IT/venv/bin/mcp_proxy http://50.116.13.109:32049/mcp/sse

# Verify operator is reachable
curl http://50.116.13.109:32049/health

# Check logs
tail -f /var/log/anylog/operator.log
```

### Query Returns Empty

**Causes:**
1. Table has no recent data
2. Time window too narrow
3. Wrong table name

**Debug:**
```bash
# Check table exists
curl "http://localhost:8080/api/tables?dbms=manufacturing_historian" | grep tic_250_001

# Check data range
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT min(timestamp), max(timestamp) FROM tic_250_001_pv_celsius"}'
```

---

## Security Considerations

**⚠️ WARNING:** This bridge has NO AUTHENTICATION. It is intended for:
- Local development only
- Trusted network environments
- Single-user desktop applications

**DO NOT:**
- Expose port 8080 to the internet
- Use in production without adding authentication
- Allow untrusted users to access the bridge

**Recommended for Production:**
1. Add API key authentication
2. Use HTTPS (TLS)
3. Implement rate limiting
4. Add SQL injection protection
5. Restrict CORS origins
6. Add request logging/auditing

---

## Performance Notes

- **Query Timeout:** 30 seconds per MCP call
- **Concurrent Requests:** Flask default (handle 1 request at a time)
- **Data Volume:** Tested with queries returning up to 10,000 rows
- **Recommended Chart Window:** ≤4 hours with 1-minute buckets (240 points)
- **Baseline Window:** 4-24 hours for UCL/LCL calculation

---

## Version History

**1.0 (2026-02-17)**
- Initial release
- Support for all MCP anylog-proveit tools
- UNS discovery endpoint
- Incremental query support
- CORS enabled for browser access

---

## Contact & Support

**Maintained by:** AnyLog Co.  
**Documentation:** https://github.com/AnyLog-co/documentation  
**MCP Specification:** https://modelcontextprotocol.io/

---

## License

MIT License - see project repository for details
