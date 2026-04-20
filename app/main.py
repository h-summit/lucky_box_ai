import json
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from openai import APIError

from app.image_index import IMAGE_TYPE_VALUES, ImageIndexError, InventoryImageDeleteService, InventoryImageIndexTaskService, InventoryImageSearchService
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
    InventoryImageDeleteResponse,
    InventoryImageIndexTaskCreateRequest,
    InventoryImageIndexTaskCreateResponse,
    InventoryImageIndexTaskDetailResponse,
    ReplyResponse,
)

app = FastAPI(title="Lucky Box AI")
image_index_task_service = InventoryImageIndexTaskService()
inventory_image_search_service = InventoryImageSearchService()
inventory_image_delete_service = InventoryImageDeleteService()
INTENT_ORDER = {
    "query_logistics": 0,
    "query_shipping_progress": 1,
    "query_inventory": 2,
    "get_quote": 3,
    "not_sure_intent": 4,
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
    """统一整理查库存结果的结构，并清洗空商品。"""
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


def _collect_image_refs(request: AnalyzeRequest) -> list[str]:
    """收集请求里的所有图片引用。"""
    all_messages = [*request.before_messages, request.at_message, *request.after_messages]
    return [message.url for message in all_messages if message.type == "image" and message.url]


def _append_image_search_inventory_items(request: AnalyzeRequest, results: list[dict]) -> list[dict]:
    """把百度图片检索命中的商品补充到查库存结果中。"""
    if not any(result.get("intent") == "query_inventory" for result in results):
        return results

    image_refs = _collect_image_refs(request)
    if not image_refs:
        return results

    try:
        image_search_items = inventory_image_search_service.search_inventory_items(image_refs)
    except ImageIndexError:
        # 图片检索是增强能力，失败时回退到原有 LLM 结果，避免主流程不可用。
        return results

    if not image_search_items:
        return results

    enriched_results = []
    for result in results:
        if result.get("intent") != "query_inventory":
            enriched_results.append(result)
            continue

        enriched_result = dict(result)
        merged_items = list(enriched_result.get("items") or [])
        merged_items.extend(image_search_items)
        enriched_result["status"] = "success"
        enriched_result["items"] = merged_items
        enriched_results.append(enriched_result)

    return enriched_results


def _merge_results(results: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}

    for result in results:
        intent = result.get("intent")
        if not intent:
            continue

        if intent == "query_logistics":
            # 兼容旧提示词仍返回 no_tracking_no 的情况，对外统一提升为独立的发货进度意图。
            if result.get("status") == "no_tracking_no":
                merged["query_shipping_progress"] = {
                    "intent": "query_shipping_progress",
                    "status": "success",
                }
                continue
            candidate = {"intent": "query_logistics"}
            if result.get("status") is not None:
                candidate["status"] = result["status"]
            if result.get("order_no"):
                candidate["order_no"] = result["order_no"]
            merged[intent] = _merge_logistics_result(merged.get(intent), candidate)
            continue

        if intent == "query_shipping_progress":
            merged[intent] = {"intent": "query_shipping_progress", "status": "success"}
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


def _normalize_analyze_results(result, request: AnalyzeRequest) -> list[dict]:
    """规范化 LLM 结果，并按需补充图片检索出的商品信息。"""
    normalized_results = [_normalize_inventory_result(item) for item in _coerce_results(result)]
    normalized_results = _append_image_search_inventory_items(request, normalized_results)
    normalized_results = [_normalize_inventory_result(item) for item in normalized_results]
    return [_minimize_analyze_result(item) for item in _merge_results(normalized_results)]


def _json_error(status_code: int, message: str) -> JSONResponse:
    """统一输出简单错误响应。"""
    return JSONResponse(status_code=status_code, content={"error": message})


@app.post(
    "/analyze_inventory_intent",
    response_model=list[AnalyzeResponse],
    response_model_exclude_none=True,
)
def analyze_inventory_intent(request: AnalyzeRequest):
    result = _handle_json_request(analyze_intent, request)
    if isinstance(result, JSONResponse):
        return result
    return [AnalyzeResponse(**item) for item in _normalize_analyze_results(result, request)]


@app.post(
    "/inventory_image_index/tasks",
    response_model=InventoryImageIndexTaskCreateResponse,
    status_code=202,
)
def create_inventory_image_index_task(request: InventoryImageIndexTaskCreateRequest):
    """创建图片检索库异步入库任务。"""
    if not request.products:
        return _json_error(400, "products 不能为空")

    task = image_index_task_service.create_task(request.products)
    image_index_task_service.start_task(task["task_id"])
    return InventoryImageIndexTaskCreateResponse(
        task_id=task["task_id"],
        status=task["status"],
        total_product_count=task["total_product_count"],
        submitted_image_count=task["submitted_image_count"],
        ignored_empty_image_count=task["ignored_empty_image_count"],
        created_at=task["created_at"],
    )


@app.get(
    "/inventory_image_index/tasks/{task_id}",
    response_model=InventoryImageIndexTaskDetailResponse,
)
def get_inventory_image_index_task(task_id: str):
    """查询图片检索库异步入库任务的执行结果。"""
    task = image_index_task_service.get_task(task_id)
    if task is None:
        return _json_error(404, "task_id 不存在")
    return InventoryImageIndexTaskDetailResponse(**task)


@app.delete(
    "/inventory_image_index/products/{code}/images/{image_type}",
    response_model=InventoryImageDeleteResponse,
)
def delete_inventory_image(code: str, image_type: str):
    """同步删除某个商品的一张图片。"""
    if image_type not in IMAGE_TYPE_VALUES:
        return _json_error(400, "image_type 不合法")

    try:
        result = inventory_image_delete_service.delete_image(code, image_type)
    except ImageIndexError as exc:
        return _json_error(502, exc.message)
    return InventoryImageDeleteResponse(**result)


@app.delete(
    "/inventory_image_index/products/{code}",
    response_model=InventoryImageDeleteResponse,
)
def delete_inventory_product(code: str):
    """同步删除某个商品当前全部已入库图片。"""
    try:
        result = inventory_image_delete_service.delete_product(code)
    except ImageIndexError as exc:
        return _json_error(502, exc.message)
    return InventoryImageDeleteResponse(**result)


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
