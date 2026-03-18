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


@app.post("/analyze_inventory_intent", response_model=AnalyzeResponse)
def analyze_inventory_intent(request: AnalyzeRequest):
    result = _handle_json_request(analyze_intent, request)
    if isinstance(result, JSONResponse):
        return result
    return AnalyzeResponse(**result)


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
