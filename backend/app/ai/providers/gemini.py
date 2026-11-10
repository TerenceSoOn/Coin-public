from __future__ import annotations
from typing import Dict, Any, List
import httpx
import json
from ..base import BaseAIProvider


class GeminiProvider(BaseAIProvider):
    """Google Gemini provider for trading decisions (REST v1beta)."""

    def __init__(self, api_key: str | None = None, **kwargs):
        super().__init__(api_key, **kwargs)
        self.model = kwargs.get("model", "gemini-1.5-pro-latest")
        self.temperature = kwargs.get("temperature", 0.7)
        self.max_output_tokens = kwargs.get("max_tokens", 8192)  # Increased from 2048
        # Endpoint format: https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key=API_KEY
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"

    def get_provider_name(self) -> str:
        return "gemini"

    def decide(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            print("[Gemini] ✗ No API key provided")
            return self._fallback_decision(payload, "No Gemini API key provided")

        try:
            prompt = self.build_trading_prompt(payload)
            structured = self.invoke_json(prompt, schema_hint=self.trading_schema())
            decision = self._normalize_trading_decision(structured, payload)
            return decision
        except Exception as exc:
            print(f"[Gemini] ✗ Error: {str(exc)}")
            return self._fallback_decision(payload, f"Gemini error: {str(exc)}")

    def invoke_json(
        self,
        prompt: str,
        *,
        schema_hint: Dict[str, Any] | None = None,
        compact: bool = False,
    ) -> Dict[str, Any]:
        try:
            response = self._call_api(prompt, schema_hint=schema_hint)
            return self._parse_response(response)
        except Exception as e:
            # Produce a schema-aware fallback so upstream roles remain functional
            print(f"[Gemini] Fallback due to error: {str(e)}")
            return self._schema_fallback(schema_hint, str(e))

    def _call_api(self, prompt: str, *, schema_hint: Dict[str, Any] | None = None, model_override: str | None = None) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        params = {"key": self.api_key}
        response_schema = schema_hint or self.trading_schema()

        base_body = {
            # Nudge the model at the system level to emit strict JSON only
            "systemInstruction": {
                "role": "system",
                "parts": [
                    {"text": "You are a professional cryptocurrency trading advisor. Always return valid JSON only, with no extra prose or code fences."}
                ],
            },
            "contents": [
                {"role": "user", "parts": [{"text": prompt}]}
            ],
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": self.max_output_tokens,
                # Request strict JSON output
                # IMPORTANT: use camelCase per REST API spec
                "responseMimeType": "application/json",
                # Constrain output to our expected schema for reliability
                "responseSchema": response_schema,
            },
        }
        model_to_use = model_override or self.model
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_to_use}:generateContent"
        
        # Retry logic for SSL/connection errors and transient HTTP errors (429/5xx/UNAVAILABLE)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=60.0) as client:
                    body = dict(base_body)
                    resp = client.post(url, params=params, headers=headers, json=body)
                    
                    if resp.status_code != 200:
                        # Inspect error payload
                        try:
                            err = resp.json().get("error", {})
                        except Exception:
                            err = {}
                        status = (err.get("status") or "").upper()

                        # Retry with fallback model if NOT_FOUND
                        if resp.status_code == 404 or status == "NOT_FOUND":
                            fallback_model = "gemini-2.5-flash"
                            print(f"[Gemini] Model {model_to_use} not found, trying {fallback_model}")
                            if model_to_use != fallback_model:
                                resp = client.post(f"https://generativelanguage.googleapis.com/v1beta/models/{fallback_model}:generateContent", params=params, headers=headers, json=body)
                                if resp.status_code != 200:
                                    raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                            else:
                                raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

                        # If INVALID_ARGUMENT likely due to responseSchema formatting, retry once without schema
                        elif resp.status_code == 400 or status == "INVALID_ARGUMENT":
                            if attempt < max_retries:
                                import time, random
                                wait_time = (2 ** attempt) + random.uniform(0, 0.5)
                                print(f"[Gemini] 400/INVALID_ARGUMENT. Retrying without responseSchema in {wait_time:.2f}s...")
                                time.sleep(wait_time)
                                body_no_schema = dict(base_body)
                                # Reduce max tokens and remove schema to maximize compatibility
                                gen_cfg = dict(body_no_schema.get("generationConfig", {}))
                                gen_cfg.pop("responseSchema", None)
                                gen_cfg["maxOutputTokens"] = min(self.max_output_tokens, 2048)
                                body_no_schema["generationConfig"] = gen_cfg
                                # Try current model first
                                resp2 = client.post(url, params=params, headers=headers, json=body_no_schema)
                                if resp2.status_code == 200:
                                    return resp2.json()
                                # Then try a stable flash model
                                resp3 = client.post(
                                    "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent",
                                    params=params,
                                    headers=headers,
                                    json=body_no_schema,
                                )
                                if resp3.status_code == 200:
                                    return resp3.json()
                                raise Exception(f"HTTP {resp3.status_code}: {resp3.text[:200]}")
                            else:
                                raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")

                        # Transient overload/throttling errors → retry with backoff
                        elif resp.status_code in (429, 500, 502, 503, 504) or status in ("UNAVAILABLE", "RESOURCE_EXHAUSTED"):
                            if attempt < max_retries - 1:
                                import time, random
                                wait_time = (2 ** attempt) + random.uniform(0, 0.5)  # jittered backoff
                                print(f"[Gemini] Transient error {resp.status_code}/{status} (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time:.2f}s...")
                                time.sleep(wait_time)
                                continue
                            else:
                                raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                        else:
                            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    
                    return resp.json()
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadTimeout) as e:
                # SSL/connection/timeout errors - retry with exponential backoff
                if attempt < max_retries - 1:
                    import time, random
                    wait_time = (2 ** attempt) + random.uniform(0, 0.5)  # jittered backoff
                    print(f"[Gemini] Connection error (attempt {attempt + 1}/{max_retries}): {str(e)}. Retrying in {wait_time:.2f}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise Exception(f"Connection failed after {max_retries} attempts: {str(e)}")
            except Exception as e:
                # Other errors - don't retry
                raise e

    def _parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
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

        try:
            # Gemini returns candidates[0].content.parts[*].text
            candidates = response.get("candidates", [])
            
            if not candidates:
                feedback = response.get("promptFeedback", {})
                raise ValueError(f"No candidates in response: {feedback}")
            
            candidate = candidates[0]
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            
            # Check for safety/blocking issues
            if finish_reason in ["SAFETY", "BLOCKED_REASON", "RECITATION", "MAX_TOKENS"]:
                safety_ratings = candidate.get("safetyRatings", [])
                raise ValueError(f"Content issue ({finish_reason}): {safety_ratings}")
            
            # Get content
            if "content" not in candidate:
                raise ValueError("No content field in response")
            
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(part.get("text", "") for part in parts)

            # Prefer direct JSON parse (expected when responseMimeType=application/json)
            try:
                decision = json.loads(text)
            except Exception as parse_error:
                print(f"[Gemini] Direct JSON parse failed: {str(parse_error)}")
                # Be resilient to unexpected wrappers (e.g., code fences) or extra tokens
                if "```json" in text:
                    print("[Gemini] Trying to extract from ```json code fence")
                    fenced = text.split("```json", 1)[1].split("```", 1)[0].strip()
                    decision = json.loads(fenced)
                elif "```" in text:
                    print("[Gemini] Trying to extract from ``` code fence")
                    fenced = text.split("```", 1)[1].split("```", 1)[0].strip()
                    decision = json.loads(fenced)
                else:
                    candidate = find_first_json(text)
                    if not candidate:
                        raise ValueError("No JSON found in response text")
                    decision = json.loads(candidate)

            return decision
        except Exception as e:
            raise ValueError(f"Parse error: {str(e)}")

    def _schema_fallback(self, schema_hint: Dict[str, Any] | None, reason: str) -> Dict[str, Any]:
        # Strategist-style schema
        if isinstance(schema_hint, dict) and isinstance(schema_hint.get("properties"), dict):
            props = schema_hint.get("properties", {})
            if all(k in props for k in ("overall_trend", "next_days_trend", "next_24h_trend", "next_few_hours_trend", "confidence")):
                return {
                    "overall_trend": "mixed",
                    "next_days_trend": "mixed",
                    "next_24h_trend": "neutral",
                    "next_few_hours_trend": "neutral",
                    "confidence": 0.5,
                    "rationale": f"Fallback due to Gemini error: {reason}",
                }
            # Tactician-style schema (has steps/status/confidence)
            if "steps" in props and "status" in props and "confidence" in props:
                return {
                    "status": "hold",
                    "confidence": 0.0,
                    "rationale": f"Fallback due to Gemini error: {reason}",
                    "plan_type": "monitor",
                    "metadata": {"plan_type": "monitor"},
                    "steps": [],
                }
        # Generic fallback
        return {
            "actions": [],
            "confidence": 0.0,
            "rationale": f"Fallback due to Gemini error: {reason}",
            "next_decision_in_minutes": 10,
        }

    def _normalize_trading_decision(self, decision: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(decision, dict):
            raise ValueError("Model response is not a JSON object")

        symbols = payload.get("symbols", [])
        actions = decision.get("actions")
        if not isinstance(actions, list):
            raise ValueError("Missing or invalid 'actions' array in response")

        normalized: List[Dict[str, Any]] = []
        for idx, action in enumerate(actions):
            if not isinstance(action, dict):
                raise ValueError(f"Action #{idx} is not an object")
            symbol = action.get("symbol") or (symbols[0] if symbols else None)
            if not symbol:
                raise ValueError("Action missing symbol and no default available")
            normalized.append(
                {
                    "symbol": symbol,
                    "action": action.get("action", "hold").lower(),
                    "size_usd": float(action.get("size_usd", 0) or 0),
                    "size_pct": float(action.get("size_pct", 0) or 0),
                    "leverage": int(action.get("leverage", 1) or 1),
                    "order_type": action.get("order_type", "market"),
                    "reason": action.get("reason"),
                }
            )

        decision["actions"] = normalized
        decision["confidence"] = float(decision.get("confidence", 0) or 0)
        decision["rationale"] = decision.get("rationale", "")
        decision["next_decision_in_minutes"] = int(decision.get("next_decision_in_minutes", payload.get("hints", {}).get("target_interval_minutes", 10)))
        return decision

    def _fallback_decision(self, payload: Dict[str, Any], reason: str) -> Dict[str, Any]:
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
