from __future__ import annotations
from typing import Dict, Any
import httpx
import json
from ..base import BaseAIProvider


class DeepSeekProvider(BaseAIProvider):
    """DeepSeek AI provider for trading decisions."""

    def __init__(self, api_key: str | None = None, **kwargs):
        super().__init__(api_key, **kwargs)
        # Use the canonical endpoint per docs; OpenAI-compatible '/v1' also works, but
        # some regions may route more reliably via the non-versioned path.
        self.base_url = "https://api.deepseek.com/chat/completions"
        # Normalize model names to current DeepSeek catalog
        self.model = self._normalize_model(kwargs.get("model", "deepseek-chat"))
        self.temperature = kwargs.get("temperature", 0.7)
        self.max_tokens = kwargs.get("max_tokens", 2000)

    def get_provider_name(self) -> str:
        return "deepseek"

    def invoke_json(
        self,
        prompt: str,
        *,
        schema_hint: Dict[str, Any] | None = None,
        compact: bool = False,
    ) -> Dict[str, Any]:
        """Call DeepSeek API and return parsed JSON response."""
        response = self._call_api(prompt)
        # Extract the message content and parse as JSON
        msg = response.get("choices", [{}])[0].get("message", {})
        content = (msg.get("content") or "")
        if not content.strip():
            content = msg.get("reasoning_content") or ""
        
        # Strip any thinking tags
        import re
        content = re.sub(r"<think>[\s\S]*?</think>", "", content, flags=re.IGNORECASE)
        
        # Try to parse JSON
        content = content.strip()
        if content.startswith("```json"):
            content = content.split("```json", 1)[1].split("```", 1)[0].strip()
        elif content.startswith("```"):
            content = content.split("```", 1)[1].split("```", 1)[0].strip()
        
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            # Fallback to finding first JSON object
            start = content.find("{")
            if start != -1:
                depth = 0
                in_str = False
                escape = False
                for idx in range(start, len(content)):
                    ch = content[idx]
                    if in_str:
                        if escape:
                            escape = False
                        elif ch == "\\":
                            escape = True
                        elif ch == '"':
                            in_str = False
                    else:
                        if ch == '"':
                            in_str = True
                        elif ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                try:
                                    return json.loads(content[start:idx+1])
                                except:
                                    pass
            raise ValueError(f"Failed to parse JSON from DeepSeek response: {e}")

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call DeepSeek API to make a trading decision.
        """
        print(f"[DeepSeek] decide() called, api_key present: {bool(self.api_key)}")
        
        if not self.api_key:
            print("[DeepSeek] ✗ No API key provided")
            return self._fallback_decision(payload, "No API key provided")

        try:
            print("[DeepSeek] Building prompt...")
            prompt = self.build_trading_prompt(payload)
            print(f"[DeepSeek] Prompt built, length: {len(prompt)} chars")
            
            print("[DeepSeek] Calling API...")
            response = self._call_api(prompt)

            print("[DeepSeek] Parsing response...")
            decision = self._parse_response(response, payload)

            # If parsing produced a fallback, attempt one recovery by switching to deepseek-chat
            if isinstance(decision, dict) and str(decision.get("rationale", "")).startswith("Fallback:") and self.model != "deepseek-chat":
                print("[DeepSeek] Parse fallback detected. Retrying once with model=deepseek-chat for strict JSON...")
                response = self._call_api(prompt, model_override="deepseek-chat")
                decision = self._parse_response(response, payload)

            okay = not str(decision.get("rationale", "")).startswith("Fallback:")
            print("[DeepSeek] " + ("✓ Decision parsed successfully" if okay else "✗ Decision fell back to HOLD"))
            return decision
        except Exception as e:
            print(f"[DeepSeek] ✗ Exception caught: type={type(e).__name__}, str={str(e)}, repr={repr(e)}")
            import traceback
            traceback.print_exc()
            return self._fallback_decision(payload, f"DeepSeek error: {str(e)}")

    def _normalize_model(self, model: str | None) -> str:
        """Map legacy/alias model names to current supported ones.
        Docs (2025-10) list: deepseek-chat (non-thinking), deepseek-reasoner (thinking).
        """
        if not model:
            return "deepseek-chat"
        m = (model or "").strip()
        mapping = {
            "deepseek-think": "deepseek-reasoner",
            "deepseek_think": "deepseek-reasoner",
            "deepseek-reason": "deepseek-reasoner",
            "deepseek_reason": "deepseek-reasoner",
            "deepseek-r1": "deepseek-reasoner",
            "deepseek_r1": "deepseek-reasoner",
        }
        normalized = mapping.get(m, m)
        if normalized != m:
            print(f"[DeepSeek] Model alias detected: '{m}' -> '{normalized}'")
        return normalized

    def _call_api(self, prompt: str, model_override: str | None = None) -> Dict[str, Any]:
        """Make the API call to DeepSeek."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        def build_payload(model_name: str) -> Dict[str, Any]:
            return {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "You are a professional cryptocurrency trading advisor. Always return valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": False,
                # Hint the model/endpoint to return strict JSON if supported (OpenAI-compatible)
                "response_format": {"type": "json_object"}
            }

        model_to_use = model_override or self.model
        print(f"[DeepSeek] Calling API with model={model_to_use}, temp={self.temperature}, max_tokens={self.max_tokens}")
        
        with httpx.Client(timeout=60.0) as client:
            # First attempt with configured/normalized model
            try:
                resp = client.post(self.base_url, headers=headers, json=build_payload(model_to_use))
            except httpx.ReadTimeout:
                print("[DeepSeek] Initial call timed out, retrying once...")
                resp = client.post(self.base_url, headers=headers, json=build_payload(model_to_use))
            print(f"[DeepSeek] Response status: {resp.status_code}")
            
            if resp.status_code != 200:
                print(f"[DeepSeek] Error response: {resp.text}")
                # Retry once if model not found
                try:
                    err = resp.json().get("error", {})
                except Exception:
                    err = {}
                msg = (err.get("message") or "").lower()
                code = err.get("code") or err.get("type") or ""
                if "model not exist" in msg or "model_not_found" in code:
                    alt = "deepseek-reasoner" if model_to_use != "deepseek-reasoner" else "deepseek-chat"
                    print(f"[DeepSeek] Model '{self.model}' not available. Retrying once with '{alt}'...")
                    resp = client.post(self.base_url, headers=headers, json=build_payload(alt))
                    print(f"[DeepSeek] Retry status: {resp.status_code}")
                    if resp.status_code != 200:
                        print(f"[DeepSeek] Retry error: {resp.text}")
                        resp.raise_for_status()
                else:
                    resp.raise_for_status()
            
            result = resp.json()
            print(f"[DeepSeek] Response received: {result.get('id', 'N/A')}")
            
            # Check for API errors in response
            if "error" in result:
                error = result["error"]
                raise Exception(f"API Error: {error.get('message', error)}")
            
            return result

    def _parse_response(self, response: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parse DeepSeek response and extract the decision JSON.
        Be resilient to code fences, empty content, <think> blocks, and extra prose.
        """
        import re

        def strip_think(text: str) -> str:
            try:
                return re.sub(r"<think>[\s\S]*?</think>", "", text, flags=re.IGNORECASE)
            except Exception:
                return text

        def find_first_json(text: str) -> str | None:
            start = text.find("{")
            while start != -1:
                depth = 0
                in_str = False
                escape = False
                for idx in range(start, len(text)):
                    ch = text[idx]
                    if in_str:
                        if escape:
                            escape = False
                        elif ch == "\\":
                            escape = True
                        elif ch == '"':
                            in_str = False
                    else:
                        if ch == '"':
                            in_str = True
                        elif ch == '{':
                            depth += 1
                        elif ch == '}':
                            depth -= 1
                            if depth == 0:
                                return text[start:idx+1]
                start = text.find("{", start + 1)
            return None

        def extract_json(text: str) -> Dict[str, Any]:
            text = strip_think(text or "")
            # Preferred: direct JSON
            if text.strip():
                try:
                    return json.loads(text)
                except Exception:
                    pass
            # Fenced JSON blocks
            if "```json" in text:
                try:
                    fenced = text.split("```json", 1)[1].split("```", 1)[0].strip()
                    return json.loads(fenced)
                except Exception:
                    pass
            if "```" in text:
                try:
                    fenced = text.split("```", 1)[1].split("```", 1)[0].strip()
                    return json.loads(fenced)
                except Exception:
                    pass
            # Balanced-brace scan for first JSON object
            candidate = find_first_json(text)
            if candidate:
                return json.loads(candidate)
            raise ValueError("No valid JSON object found in model output")

        try:
            msg = response.get("choices", [{}])[0].get("message", {})
            content = (msg.get("content") or "")
            if not content.strip():
                # Fall back to reasoning_content if present
                content = msg.get("reasoning_content") or ""
            print(f"[DeepSeek] Raw content: {content[:200]}...")
            decision = extract_json(content)
            print(f"[DeepSeek] Parsed decision: actions={len(decision.get('actions', []))}, confidence={decision.get('confidence', 0)}")
            if "actions" not in decision:
                raise ValueError("Missing 'actions' field in response")
            return decision
        except Exception as e:
            return self._fallback_decision(payload, f"Parse error: {str(e)}")

    def _fallback_decision(self, payload: Dict[str, Any], reason: str) -> Dict[str, Any]:
        """Return a safe 'hold' decision if something goes wrong."""
        symbols = payload.get("symbols", [])
        hints = payload.get("hints", {})
        return {
            "actions": [
                {"symbol": sym, "action": "hold", "order_type": "market", "size_usdt": 0, "leverage": 1}
                for sym in symbols
            ],
            "next_decision_in_minutes": hints.get("target_interval_minutes", 10),
            "rationale": f"Fallback: {reason}",
            "confidence": 0.0,
        }


# Legacy function for backward compatibility
def decide(payload: Dict[str, Any], api_key: str | None = None) -> Dict[str, Any]:
    provider = DeepSeekProvider(api_key=api_key)
    return provider.decide(payload)
