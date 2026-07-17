import os
import re
import json
from typing import Dict, Any
import requests

# Use a more stable model
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
)


def _strip_md_code_blocks(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


# Updated function
def _extract_text_from_response(resp_json: Dict[str, Any]) -> str:
    try:
        candidate = resp_json["candidates"][0]
        parts = candidate["content"]["parts"]
        return "".join(part.get("text", "") for part in parts)
    except Exception:
        return ""


def check_message(message_content: str) -> Dict[str, Any]:
    """Check message toxicity using Gemini"""

    if not GEMINI_API_KEY:
        return {
            "is_allowed": True,
            "category": "unknown",
            "confidence": 0.0,
            "reason": "no_api_key",
        }

    prompt = (
        "Classify the following user message into one of: clean, toxic, spam, harassment. "
        "Respond ONLY with JSON: "
        '{"category":"clean|toxic|spam|harassment","confidence":0.95,"reason":"..."}'
        f"\n\nMessage:\n{message_content}"
    )

    url = f"{GEMINI_ENDPOINT}?key={GEMINI_API_KEY}"

    headers = {
        "Content-Type": "application/json",
    }

    body = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 256
        }
    }

    try:
        r = requests.post(url, headers=headers, json=body, timeout=10)

        print("STATUS:", r.status_code)
        print("RESPONSE:", r.text)

        r.raise_for_status()

        resp_json = r.json()
        raw_text = _extract_text_from_response(resp_json)

        if not raw_text:
            raw_text = json.dumps(resp_json)

        clean = _strip_md_code_blocks(raw_text)

        try:
            parsed = json.loads(clean)
        except Exception:
            match = re.search(r"\{.*\}", clean, flags=re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                except Exception:
                    parsed = None
            else:
                parsed = None

        if not parsed or not isinstance(parsed, dict):
            return {
                "is_allowed": True,
                "category": "unknown",
                "confidence": 0.0,
                "reason": "unparseable_response",
            }

        category = parsed.get("category", "unknown")
        confidence = float(parsed.get("confidence", 0.0))
        reason = parsed.get("reason", "")

        confidence = max(0.0, min(confidence, 1.0))
        is_allowed = category.lower() == "clean" or confidence < 0.5

        return {
            "is_allowed": is_allowed,
            "category": category,
            "confidence": confidence,
            "reason": str(reason),
        }

    except Exception as e:
        print("ERROR:", str(e))
        return {
            "is_allowed": True,
            "category": "unknown",
            "confidence": 0.0,
            "reason": "api_error",
        }


if __name__ == "__main__":
    print(check_message("I will kill you"))