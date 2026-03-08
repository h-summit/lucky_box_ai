import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from openai import APIError

from app.llm import analyze_intent
from app.schemas import AnalyzeRequest, AnalyzeResponse

app = FastAPI(title="Lucky Box AI")


@app.post("/analyze_inventory_intent", response_model=AnalyzeResponse)
def analyze_inventory_intent(request: AnalyzeRequest):
    try:
        result = analyze_intent(request)
    except APIError as e:
        return JSONResponse(status_code=e.status_code, content={"error": str(e.message)})
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=502, content={"error": f"LLM 返回了非 JSON 内容: {e.doc}"})
    return AnalyzeResponse(**result)
