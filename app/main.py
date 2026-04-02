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


def _handle_json_request(handler: Callable, request):
    try:
        return handler(request)
    except APIError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e.message)})
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=502, content={"error": f"LLM 返回了非 JSON 内容: {e.doc}"})


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

    normalized["items"] = items or None
    normalized.pop("item_code", None)
    normalized.pop("item_name", None)
    return normalized


def _minimize_analyze_result(result: dict) -> dict:
    minimized = {"intent": result["intent"]}
    if "status" in result and result["status"] is not None:
        minimized["status"] = result["status"]
    if "order_no" in result and result["order_no"] is not None:
        minimized["order_no"] = result["order_no"]
    if "items" in result and result["items"] is not None:
        minimized["items"] = result["items"]
    return minimized


@app.post("/analyze_inventory_intent", response_model=AnalyzeResponse)
def analyze_inventory_intent(request: AnalyzeRequest):
    result = _handle_json_request(analyze_intent, request)
    if isinstance(result, JSONResponse):
        return result
    return AnalyzeResponse(**_minimize_analyze_result(_normalize_inventory_result(result)))


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
