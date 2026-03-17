# AnyLog REST Proxy

A Python-based REST proxy that provides a standardized HTTP API interface to AnyLog networks using AnyLog's native REST API. All requests include the required `User-Agent: AnyLog/1.23` header.

## Features

- **Native AnyLog REST API**: Direct communication with AnyLog nodes via their REST interface
- **Standard HTTP Endpoints**: Clean REST API for querying and managing AnyLog networks
- **Required Headers**: Automatically includes proper headers on all requests:
  - `User-Agent: AnyLog/1.23` - Required for all requests
  - `command: <command>` - The AnyLog command being executed
  - `destination: network` - Added for SQL queries to route across network
- **Full Query Support**: SQL queries and time-series increment queries
- **Metadata Discovery**: List databases, tables, columns, and nodes
- **Node Management**: Query status and cluster information
- **Arbitrary Commands**: Execute any AnyLog command via REST
- **Connection Testing**: Built-in connection health checks
- **CORS Enabled**: Ready for browser-based applications

## Installation

### Prerequisites

- Python 3.8+
- pip

### Setup

```bash
# Install dependencies
pip install flask flask-cors requests

# Make scripts executable
chmod +x anylog_rest_proxy.py start_rest_proxy.sh
```

## Usage

### Quick Start

```bash
# Start with default settings (connects to 172.79.89.206:32049)
./start_rest_proxy.sh

# Specify custom AnyLog node
./start_rest_proxy.sh --anylog-ip 50.116.13.109 --anylog-port 32049

# Run on different port with debug mode
./start_rest_proxy.sh --port 5000 --debug
```

### Manual Start

```bash
python3 anylog_rest_proxy.py --host 0.0.0.0 --port 8080 --anylog-ip 172.79.89.206 --anylog-port 32049
```

### Command-Line Options

```
--host HOST           Host to bind to (default: 0.0.0.0)
--port PORT           Proxy port (default: 8080)
--anylog-ip IP        AnyLog node IP (default: 172.79.89.206)
--anylog-port PORT    AnyLog node port (default: 32049)
--debug               Enable debug mode
```

## Connection Details

The proxy connects to AnyLog using:
- **Protocol**: HTTP (not HTTPS)
- **Method**: GET
- **URL**: `http://{IP}:{PORT}`
- **Headers**: 
  - `User-Agent: AnyLog/1.23` (all requests)
  - `Content-Type: application/json` (all requests)
  - `command: <anylog_command>` (all requests - the command is in the header)
  - `destination: network` (SQL queries only)
- **Body**: Empty (command is passed in header)

### Header Examples

**Non-SQL Command** (e.g., `get status`):
```
GET http://172.79.89.206:32049
User-Agent: AnyLog/1.23
Content-Type: application/json
command: get status
```

**SQL Query** (with `destination: network` for distributed query):
```
GET http://172.79.89.206:32049
User-Agent: AnyLog/1.23
Content-Type: application/json
command: sql litsanleandro SELECT * FROM ping_sensor LIMIT 10
destination: network
```

### When is `destination: network` Used?

The `destination: network` header is automatically added for **SQL queries only**. This tells AnyLog to distribute the query across the network to all relevant nodes.

- ✓ **SQL queries**: `destination: network` header is added
- ✗ **Non-SQL commands**: No `destination` header (commands like `get status`, `get databases`, etc.)

This ensures SQL queries are properly distributed while administrative commands run locally on the target node.

## API Endpoints

### Health & Connection

#### Health Check
```bash
GET /health
```

Returns proxy health and AnyLog connection status.

Example:
```bash
curl http://localhost:8080/health
```

Response:
```json
{
  "status": "healthy",
  "service": "anylog-rest-proxy",
  "anylog_node": "172.79.89.206:32049",
  "connection": "established",
  "user_agent": "AnyLog/1.23"
}
```

#### Connection Status
```bash
GET /api/connection/status
```

Get detailed connection information.

Example:
```bash
curl http://localhost:8080/api/connection/status
```

#### Test Connection
```bash
POST /api/connection/test
```

Test connection to AnyLog node.

Example:
```bash
curl -X POST http://localhost:8080/api/connection/test
```

### Query Execution

#### Execute SQL Query
```bash
POST /api/query
Content-Type: application/json

{
  "dbms": "database_name",
  "sql": "SELECT * FROM table WHERE condition",
  "destination": "optional_destination"
}
```

Example:
```bash
curl -X POST http://localhost:8080/api/query \
  -H "Content-Type: application/json" \
  -d '{
    "dbms": "litsanleandro",
    "sql": "SELECT * FROM ping_sensor WHERE timestamp >= NOW() - 1 hour LIMIT 10"
  }'
```

#### Execute Time-Series Query with Increments
```bash
POST /api/query/increment
Content-Type: application/json

{
  "dbms": "database_name",
  "table": "table_name",
  "timeColumn": "timestamp",
  "startTime": "2024-01-01 00:00:00",
  "endTime": "2024-01-02 00:00:00",
  "timeUnit": "hour",
  "intervalLength": 1,
  "projections": ["avg(value)", "max(value)"],
  "destination": "optional_destination"
}
```

Example:
```bash
curl -X POST http://localhost:8080/api/query/increment \
  -H "Content-Type: application/json" \
  -d '{
    "dbms": "litsanleandro",
    "table": "ping_sensor",
    "timeColumn": "timestamp",
    "startTime": "NOW() - 24 hours",
    "endTime": "NOW()",
    "timeUnit": "hour",
    "intervalLength": 1,
    "projections": ["min(value)", "avg(value)", "max(value)"]
  }'
```

### Metadata Discovery

#### List Databases
```bash
GET /api/databases
```

Example:
```bash
curl http://localhost:8080/api/databases
```

#### List Tables
```bash
GET /api/databases/{dbms}/tables
```

Example:
```bash
curl http://localhost:8080/api/databases/litsanleandro/tables
```

#### List Columns
```bash
GET /api/databases/{dbms}/tables/{table}/columns
```

Example:
```bash
curl http://localhost:8080/api/databases/litsanleandro/tables/ping_sensor/columns
```

### Node Management

#### Get Cluster Nodes
```bash
GET /api/nodes
```

Example:
```bash
curl http://localhost:8080/api/nodes
```

#### Get Node Status
```bash
GET /api/nodes/status?node=<optional_node>
```

Or:
```bash
POST /api/nodes/status
Content-Type: application/json

{
  "node": "optional_node_address"
}
```

Example:
```bash
# Get status of current node
curl http://localhost:8080/api/nodes/status

# Get status of specific node
curl http://localhost:8080/api/nodes/status?node=172.105.60.50:32148
```

#### Get Data Location
```bash
GET /api/data/location?dbms=<dbms>&table=<table>
```

Example:
```bash
curl "http://localhost:8080/api/data/location?dbms=litsanleandro&table=ping_sensor"
```


### UNS Policies (Blockchain Root Policies)

#### Get UNS Root Policies
```bash
GET /api/uns
```

This endpoint proxies the native AnyLog command:

- `blockchain get uns`

Example:
```bash
curl http://localhost:8080/api/uns
```

Response:
```json
{
  "command": "blockchain get uns",
  "policies": [],
  "raw": "..."
}
```


### Execute Arbitrary Command

```bash
POST /api/command
Content-Type: application/json

{
  "command": "get status",
  "timeout": 30
}
```

Example:
```bash
curl -X POST http://localhost:8080/api/command \
  -H "Content-Type: application/json" \
  -d '{
    "command": "get cluster info"
  }'
```

## Python Client Examples

### Basic Query

```python
import requests

# Execute a simple query
response = requests.post(
    'http://localhost:8080/api/query',
    json={
        'dbms': 'litsanleandro',
        'sql': 'SELECT * FROM ping_sensor WHERE timestamp >= NOW() - 1 hour'
    }
)

result = response.json()
print(result)
```

### Time-Series Query

```python
import requests

# Execute time-series query with hourly aggregations
response = requests.post(
    'http://localhost:8080/api/query/increment',
    json={
        'dbms': 'litsanleandro',
        'table': 'ping_sensor',
        'timeColumn': 'timestamp',
        'startTime': 'NOW() - 24 hours',
        'endTime': 'NOW()',
        'timeUnit': 'hour',
        'intervalLength': 1,
        'projections': [
            'min(value)',
            'avg(value)',
            'max(value)'
        ]
    }
)

result = response.json()
print(result)
```

### Execute Custom Command

```python
import requests

# Execute any AnyLog command
response = requests.post(
    'http://localhost:8080/api/command',
    json={
        'command': 'get virtual tables',
        'timeout': 30
    }
)

result = response.json()
print(result)
```

### Discover Database Schema

```python
import requests

base_url = 'http://localhost:8080'

# List all databases
databases = requests.get(f'{base_url}/api/databases').json()
print("Databases:", databases)

# List tables in a database
tables = requests.get(
    f'{base_url}/api/databases/litsanleandro/tables'
).json()
print("Tables:", tables)

# List columns in a table
columns = requests.get(
    f'{base_url}/api/databases/litsanleandro/tables/ping_sensor/columns'
).json()
print("Columns:", columns)
```

## Bash Script Examples

### Query Wrapper Script

```bash
#!/bin/bash
# query_anylog.sh - Simple wrapper for querying AnyLog

PROXY_URL="http://localhost:8080"

query_data() {
    local dbms="$1"
    local sql="$2"
    
    curl -s -X POST "$PROXY_URL/api/query" \
        -H "Content-Type: application/json" \
        -d "{\"dbms\":\"$dbms\",\"sql\":\"$sql\"}"
}

# Usage
query_data "litsanleandro" "SELECT * FROM ping_sensor LIMIT 5"
```

### Command Execution Script

```bash
#!/bin/bash
# anylog_command.sh - Execute AnyLog commands

PROXY_URL="http://localhost:8080"

execute_command() {
    local command="$1"
    
    curl -s -X POST "$PROXY_URL/api/command" \
        -H "Content-Type: application/json" \
        -d "{\"command\":\"$command\"}"
}

# Usage
execute_command "get status"
execute_command "get cluster info"
```

## How It Works

The proxy translates REST API calls into native AnyLog REST commands:

```
┌─────────────┐      HTTP REST      ┌──────────────┐  AnyLog REST (HTTP) ┌─────────────┐
│   Client    │ ──────────────────> │    Proxy     │ ──────────────────> │   AnyLog    │
│ Application │ <────────────────── │    Server    │ <────────────────── │    Node     │
└─────────────┘      JSON           └──────────────┘    JSON/Text        └─────────────┘
                                           ↓
                                   GET http://IP:PORT
                                   Headers:
                                   - User-Agent: AnyLog/1.23
                                   - command: <anylog_command>
                                   - destination: network (SQL only)
```

The proxy:
1. Receives REST requests on standard HTTP endpoints
2. Translates requests into AnyLog commands
3. Adds commands and metadata as HTTP headers:
   - `User-Agent: AnyLog/1.23`
   - `command: <anylog_command>` (the actual command)
   - `destination: network` (for SQL queries)
4. Sends GET request to `http://IP:PORT`
5. Returns JSON responses to client

**Important**: The command is passed entirely in the `command:` header using HTTP GET method.

## AnyLog Command Mapping

| REST Endpoint | AnyLog Command |
|--------------|----------------|
| `GET /api/databases` | `get databases` |
| `GET /api/databases/{dbms}/tables` | `get tables where dbms = {dbms}` |
| `GET /api/databases/{dbms}/tables/{table}/columns` | `get columns where dbms = {dbms} and table = {table}` |
| `GET /api/nodes` | `get cluster info` |
| `GET /api/nodes/status` | `get status` |
| `POST /api/query` | `sql {dbms} {sql}` |
| `POST /api/query/increment` | `sql {dbms} SELECT increment(...) ...` |
| `GET /api/data/location` | `get data nodes` |
| `GET /api/uns` | `blockchain get uns` |

## Configuration

### Environment Variables

You can configure via environment variables:

```bash
export ANYLOG_IP="172.79.89.206"
export ANYLOG_PORT="32049"
export PROXY_HOST="0.0.0.0"
export PROXY_PORT="8080"

python3 anylog_rest_proxy.py
```

### Timeout Configuration

Modify the `TIMEOUT` constant in the script for longer queries:

```python
TIMEOUT = 300.0  # 5 minutes (default)
```

## Error Handling

All endpoints return standard HTTP status codes:

- `200 OK`: Successful request
- `400 Bad Request`: Missing or invalid parameters
- `404 Not Found`: Endpoint doesn't exist
- `500 Internal Server Error`: AnyLog node error or internal error
- `503 Service Unavailable`: Connection to AnyLog node failed

Error responses include details:

```json
{
  "error": "Error description",
  "details": "Additional error information"
}
```

## Troubleshooting

### Connection Issues

```bash
# Test AnyLog node connectivity with proper headers
curl http://172.79.89.206:32049 \
  -H "User-Agent: AnyLog/1.23" \
  -H "command: get status"

# Check proxy health
curl http://localhost:8080/health

# Test proxy connection to AnyLog
curl -X POST http://localhost:8080/api/connection/test
```

### Timeout Issues

For long-running queries, increase timeout:

```python
TIMEOUT = 600.0  # 10 minutes
```

## Differences from MCP Proxy

This REST proxy differs from the MCP-based proxy:

| Feature | REST Proxy | MCP Proxy |
|---------|-----------|-----------|
| Protocol | AnyLog native REST | MCP over SSE |
| Headers | User-Agent: AnyLog/1.23 | Standard MCP headers |
| Connection | Direct HTTP POST | SSE event stream |
| Commands | Native AnyLog syntax | MCP tool calls |
| Response | JSON/Text | SSE events |

## License

This proxy is provided as-is for use with AnyLog distributed networks.

## Support

For issues specific to:
- AnyLog networks: Contact AnyLog Co
- This proxy: Submit issues to your internal repository
