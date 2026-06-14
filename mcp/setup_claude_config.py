"""
Run this once to create the Claude Desktop MCP config file.
Usage: python mcp/setup_claude_config.py
"""
import os, json

config = {
  "mcpServers": {
    "digicrm": {
      "command": "C:\\ritik\\AAAAA\\digicrm\\venv\\Scripts\\python.exe",
      "args": ["-m", "mcp.server"],
      "cwd": "C:\\ritik\\AAAAA\\digicrm",
      "env": {
        "DIGICRM_BASE_URL": "http://localhost:8000",
        "DIGICRM_JWT_TOKEN": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzgzMTA4OTY3LCJpYXQiOjE3ODEzODA5NjcsImp0aSI6ImI3OGJmMTVmYzdmYjRjYzFhMmE3YjVhZTRlYzIyZTU0IiwidXNlcl9pZCI6ImZmYmZmYTBhLTYyMmQtNGFmMy1iMmUyLTJkNDI2MGQwNDM2ZiIsImVtYWlsIjoiZGlnaXRlY2hAZ21haWwuY29tIiwidGVuYW50X2lkIjoiZmU4MTQyM2ItYTViYy00MWQwLTkzYmYtZTMxMWI3YjcxZTFjIiwidGVuYW50X3NsdWciOiJkaWdpdGVjaCIsImlzX3N1cGVyX2FkbWluIjp0cnVlLCJwZXJtaXNzaW9ucyI6e30sImVuYWJsZWRfbW9kdWxlcyI6WyJjcm0iLCJ3aGF0c2FwcCIsIm1lZXRpbmdzIiwicGF5bWVudHMiLCJpbnRlZ3JhdGlvbnMiLCJhZG1pbiIsImhtcyIsInNtYXJ0aHJpbiIsInZvaWNlYWkiLCJ0ZWxlcGhvbnkiXX0.xqy8RjeIaAvTI0lVcPOdwAcLZMZp7qcgDbgGL_LXYls",
        "DIGICRM_TENANT_ID": "fe81423b-a5bc-41d0-93bf-e311b7b71e1c"
      }
    }
  }
}

# Find the right folder
claude_dir = os.path.join(os.environ['APPDATA'], 'Claude')
os.makedirs(claude_dir, exist_ok=True)

config_path = os.path.join(claude_dir, 'claude_desktop_config.json')
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)

print(f"✅ Config written to: {config_path}")
print("Now: fully quit Claude Desktop (system tray → Quit) and reopen it.")
