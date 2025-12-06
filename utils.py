import json
import re

def extract_json_from_text(text: str) -> dict:
    # ```json ... ``` 우선
    m = re.search(r"```json(.*?)```", text, re.DOTALL)
    if m:
        json_str = m.group(1).strip()
        return json.loads(json_str)

    # fallback: 처음 '{' ~ 마지막 '}'
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        json_str = text[start:end+1]
        return json.loads(json_str)

    raise ValueError("텍스트에서 JSON 블록을 찾지 못함")


def parse_json_safe(raw):
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            try:
                start = raw.index("{")
                end = raw.rfind("}")
                return json.loads(raw[start:end+1])
            except Exception:
                return None
    return None
