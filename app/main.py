import json
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from openai import APIError

from app.llm import (
    analyze_intent,
    generate_customer_relationship_management,
    generate_greetings,
    generate_holiday_greetings,
)
from app.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    CustomerRelationshipManagementRequest,
    GreetingsRequest,
    HolidayGreetingsRequest,
    ReplyResponse,
)

app = FastAPI(title="Lucky Box AI")
INTENT_ORDER = {
    "query_logistics": 0,
    "query_inventory": 1,
    "get_quote": 2,
    "not_sure_intent": 3,
}


def _handle_json_request(handler: Callable, request):
    try:
        return handler(request)
    except APIError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e.message)})
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=502, content={"error": f"LLM 返回了非 JSON 内容: {e.doc}"})


def _coerce_results(result) -> list[dict]:
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    if isinstance(result, dict):
        return [result]
    return []


def _normalize_inventory_result(result: dict) -> dict:
    normalized = dict(result)
    if normalized.get("intent") != "query_inventory" or normalized.get("status") != "success":
        normalized.pop("item_code", None)
        normalized.pop("item_name", None)
        return normalized

    raw_items = normalized.get("items")
    if raw_items is None:
        raw_items = [{
            "item_code": normalized.get("item_code"),
            "item_name": normalized.get("item_name"),
        }]
    elif isinstance(raw_items, dict):
        raw_items = [raw_items]

    items = []
    seen = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue

        item = {}
        if raw_item.get("item_code"):
            item["item_code"] = raw_item["item_code"]
        if raw_item.get("item_name"):
            item["item_name"] = raw_item["item_name"]
        if not item:
            continue

        key = tuple(sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        items.append(item)

    if items:
        normalized["items"] = items
    else:
        normalized["status"] = "no_info_extracted"
        normalized.pop("items", None)
    normalized.pop("item_code", None)
    normalized.pop("item_name", None)
    return normalized


def _merge_logistics_result(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate

    current_has_order = bool(current.get("order_no"))
    candidate_has_order = bool(candidate.get("order_no"))
    if candidate_has_order and not current_has_order:
        return candidate
    if candidate_has_order == current_has_order and candidate.get("status") == "success" and current.get("status") != "success":
        return candidate
    return current


def _merge_inventory_result(current: dict | None, candidate: dict) -> dict:
    if current is None:
        return candidate

    current_items = current.get("items") or []
    candidate_items = candidate.get("items") or []
    merged_items = []
    seen = set()

    for item in [*current_items, *candidate_items]:
        if not isinstance(item, dict):
            continue
        key = tuple(sorted(item.items()))
        if key in seen:
            continue
        seen.add(key)
        merged_items.append(item)

    if merged_items:
        return {
            "intent": "query_inventory",
            "status": "success",
            "items": merged_items,
        }

    return current if current.get("status") == "no_info_extracted" else candidate


def _merge_results(results: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}

    for result in results:
        intent = result.get("intent")
        if not intent:
            continue

        if intent == "query_logistics":
            candidate = {"intent": "query_logistics"}
            if result.get("status") is not None:
                candidate["status"] = result["status"]
            if result.get("order_no"):
                candidate["order_no"] = result["order_no"]
            merged[intent] = _merge_logistics_result(merged.get(intent), candidate)
            continue

        if intent == "query_inventory":
            candidate = {"intent": "query_inventory"}
            if result.get("status") is not None:
                candidate["status"] = result["status"]
            if result.get("items") is not None:
                candidate["items"] = result["items"]
            merged[intent] = _merge_inventory_result(merged.get(intent), candidate)
            continue

        if intent == "get_quote":
            merged[intent] = {"intent": "get_quote", "status": "success"}
            continue

        if intent == "not_sure_intent":
            merged.setdefault(intent, {"intent": "not_sure_intent"})
            continue

        merged[intent] = result

    concrete_intents = [intent for intent in merged if intent != "not_sure_intent"]
    if concrete_intents:
        merged.pop("not_sure_intent", None)

    if not merged:
        return [{"intent": "not_sure_intent"}]

    return sorted(
        merged.values(),
        key=lambda item: INTENT_ORDER.get(item["intent"], len(INTENT_ORDER)),
    )


def _minimize_analyze_result(result: dict) -> dict:
    minimized = {"intent": result["intent"]}
    if result.get("status") is not None:
        minimized["status"] = result["status"]
    if result.get("order_no") is not None:
        minimized["order_no"] = result["order_no"]
    if result.get("items") is not None:
        minimized["items"] = result["items"]
    return minimized


def _normalize_analyze_results(result) -> list[dict]:
    normalized_results = [_normalize_inventory_result(item) for item in _coerce_results(result)]
    return [_minimize_analyze_result(item) for item in _merge_results(normalized_results)]


@app.post(
    "/analyze_inventory_intent",
    response_model=list[AnalyzeResponse],
    response_model_exclude_none=True,
)
def analyze_inventory_intent(request: AnalyzeRequest):
    result = _handle_json_request(analyze_intent, request)
    if isinstance(result, JSONResponse):
        return result
    return [AnalyzeResponse(**item) for item in _normalize_analyze_results(result)]


@app.post("/greetings", response_model=ReplyResponse)
def greetings(request: GreetingsRequest):
    result = _handle_json_request(generate_greetings, request)
    if isinstance(result, JSONResponse):
        return result
    return ReplyResponse(**result)


@app.post("/holiday_greetings", response_model=ReplyResponse)
def holiday_greetings(request: HolidayGreetingsRequest):
    result = _handle_json_request(generate_holiday_greetings, request)
    if isinstance(result, JSONResponse):
        return result
    return ReplyResponse(**result)


@app.post("/customer_relationship_management", response_model=ReplyResponse)
def customer_relationship_management(request: CustomerRelationshipManagementRequest):
    result = _handle_json_request(generate_customer_relationship_management, request)
    if isinstance(result, JSONResponse):
        return result
    return ReplyResponse(**result)
