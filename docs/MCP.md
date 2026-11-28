# MCP server (DMS_MCP_V2)

Standards MCP endpoint at `/mcp` (Streamable HTTP, JSON-RPC 2.0). Default off.
Set `DMS_MCP_V2=1` to mount it. `DMS_MCP` and `POST /v1/mcp/call` are unchanged.

The official MCP Python SDK is the optional extra `mcp`, pin `mcp==2.3.0`, MIT.
A process that leaves the flag off does not import it.

```
pip install '.[mcp]'
```

## Auth

`DMS_API_KEYS` is `role:secret` pairs separated by `;`. Roles are `viewer`,
`steward`, and `admin`. The actor is `api_<role>`. There is no anonymous call
and no built-in demo key. Send the secret only as `Authorization: Bearer`.
The server never echoes it. Each actor is limited to 20 requests per 60
seconds.

```
DMS_MCP_V2=1
DMS_API_KEYS=steward:<secret>
```

## Tools

All six are read-only. They call the existing HTTP handlers in-process.

| Tool | Handler |
| --- | --- |
| ask | POST /v1/chat/ask |
| answer_clarify | POST /v1/chat/clarify/pick |
| list_spaces | GET /v1/spaces |
| list_metrics | GET /v1/ontology/metrics |
| describe_space | GET /v1/spaces/{id}/ontology, with row payloads removed |
| get_proof | SQL, sources, and route stamp for a proof id from this caller's ask |

If `ask` returns a clarify and the client declared form elicitation, the
readings are an elicitation. A bare capability, a url-only capability, or no
capability returns the clarify envelope as the tool result. A declined or
unknown reading is not answered.

When `DMS_ASK_STREAM=1` and the client sends a progress token, ask stages are
`notifications/progress` messages of the form `stage:status`. Those messages
do not carry SQL or cell values.

## Connect

URL: `https://<host>/mcp`

Header: `Authorization: Bearer <secret>`

Claude Desktop and Cursor (`mcp.json`):

```json
{
  "mcpServers": {
    "dms": {
      "url": "https://<host>/mcp",
      "headers": {
        "Authorization": "Bearer <secret>"
      }
    }
  }
}
```

Cortex Agents and Genie attach the same URL as a remote MCP server and send
the same bearer header. DMS does not pick a model. Model routing stays in
OpenVault; this server does not send a provider or a model id.
