"""scripts/p1_studio_ai_probe.py

Manual probe script (NOT in gates) to send at most 5 REAL Gemini requests
in the exact shape the Studio adapter sends.
Zero secrets printed or written to disk.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.gateway.studio_schemas import BUNDLED_STUDIO_TOOLS


def redact_secrets(text: str) -> str:
    """Redact any potential API keys or tokens from text."""
    text = re.sub(r"AIza[0-9A-Za-z\-_]{35}", "[REDACTED_API_KEY]", text)
    text = re.sub(
        r"(key|api_key|token|secret)=([^\s&\"']+)",
        r"\1=[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text


def main() -> None:
    url = "http://127.0.0.1:8124/ai/studio"
    print(f"=== PROBING {url} ===")

    # Shape 1: Exact Studio adapter shape with AG Studio tool schemas
    # (containing additionalProperties: false, type: ["string", "null"], etc.)
    real_studio_tools = []
    for name, tool in list(BUNDLED_STUDIO_TOOLS.items())[:3]:
        t = json.loads(json.dumps(tool))
        # AG Studio's shape adds additionalProperties: false and optional null types
        t["parameters"]["additionalProperties"] = False
        t["parameters"]["$schema"] = "http://json-schema.org/draft-07/schema#"
        real_studio_tools.append(t)

    requests_to_send = [
        # Request 1: First turn with prompt starter and Studio tools containing JSON schema keywords
        {
            "label": "Turn 1: Starter prompt with Studio tools (JSON-Schema keywords)",
            "payload": {
                "input": [
                    {
                        "id": "msg_turn_1",
                        "kind": "input",
                        "type": "message",
                        "role": "user",
                        "status": "completed",
                        "content": [
                            {
                                "type": "text",
                                "text": "Which root cause has the most revenue at risk?",
                            }
                        ],
                    }
                ],
                "responseFormat": {"type": "text"},
                "tools": real_studio_tools,
                "toolChoice": "auto",
                "threadId": "probe_thread_1",
            },
        },
        # Request 2: Normal Studio adapter call without client tools (default bundled tools)
        {
            "label": "Turn 2: Standard starter prompt without explicit tools array",
            "payload": {
                "input": [
                    {
                        "id": "msg_turn_2",
                        "kind": "input",
                        "type": "message",
                        "role": "user",
                        "status": "completed",
                        "content": [
                            {
                                "type": "text",
                                "text": "Show verified vs asserted recoveries",
                            }
                        ],
                    }
                ],
                "responseFormat": {"type": "text"},
                "threadId": "probe_thread_2",
            },
        },
    ]

    with httpx.Client(timeout=30.0) as client:
        for idx, item in enumerate(requests_to_send, 1):
            print(f"\n--- Request {idx}: {item['label']} ---")
            try:
                resp = client.post(url, json=item["payload"])
                print(f"HTTP Status: {resp.status_code}")
                content_type = resp.headers.get("content-type", "")
                print(f"Content-Type: {content_type}")
                body = redact_secrets(resp.text)
                if resp.status_code == 200:
                    first_lines = "\n".join(body.splitlines()[:8])
                    print(f"Response (first lines):\n{first_lines}")
                else:
                    print(f"Error Body:\n{body}")
            except Exception as exc:
                print(f"Exception: {type(exc).__name__}: {redact_secrets(str(exc))}")


if __name__ == "__main__":
    main()
