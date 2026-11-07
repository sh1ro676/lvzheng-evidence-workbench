from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import server  # noqa: E402
from pkulaw_mcp import ENDPOINTS, PkulawError  # noqa: E402

ENDPOINTS.update({
    "case_semantic": "https://apim-gateway.pkulaw.com/mcp-case-search-service",
    "case_keyword": "https://apim-gateway.pkulaw.com/mcp-case",
    "law_recognition": "https://apim-gateway.pkulaw.com/law_recognition",
    "case_number_recognition": "https://apim-gateway.pkulaw.com/case_number_recognition",
    "citation_validator": "https://apim-gateway.pkulaw.com/pku_citation_validator",
    "doc_link": "https://apim-gateway.pkulaw.com/add-doc-link",
})


def main() -> None:
    if not server.PKULAW.configured:
        print(json.dumps({"configured": False}, ensure_ascii=False))
        return
    output = {"configured": True, "services": {}}
    for service in ENDPOINTS:
        try:
            tools = server.PKULAW.client.list_tools(service)
            output["services"][service] = {
                "ok": True,
                "tools": [
                    {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "required": (tool.get("inputSchema") or {}).get("required", []),
                        "properties": list(((tool.get("inputSchema") or {}).get("properties") or {}).keys()),
                    }
                    for tool in tools
                ],
            }
        except PkulawError as error:
            output["services"][service] = {"ok": False, "error": str(error)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
