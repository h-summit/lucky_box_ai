import json

from openai import OpenAI

from app.config import settings
from app.prompts import SYSTEM_PROMPT
from app.schemas import AnalyzeRequest


def _has_image(request: AnalyzeRequest) -> bool:
    """检查消息中是否包含图片。"""
    all_messages = [*request.before_messages, request.at_message, *request.after_messages]
    return any(m.type == "image" for m in all_messages)


def _build_user_content(request: AnalyzeRequest) -> list:
    """将请求消息构建为 OpenAI messages content 格式。"""
    parts = []

    def _add_messages(label: str, messages):
        for msg in messages:
            if msg.type == "text" and msg.content:
                parts.append({"type": "text", "text": f"[{label}] {msg.content}"})
            elif msg.type == "image" and msg.url:
                parts.append({"type": "text", "text": f"[{label}] (图片):"})
                parts.append({"type": "image_url", "image_url": {"url": msg.url}})

    _add_messages("前文消息", request.before_messages)
    _add_messages("@AI消息", [request.at_message])
    _add_messages("后续消息", request.after_messages)

    return parts


def _build_user_text(request: AnalyzeRequest) -> str:
    """将请求消息构建为纯文本格式。"""
    lines = []

    def _add_messages(label: str, messages):
        for msg in messages:
            if msg.type == "text" and msg.content:
                lines.append(f"[{label}] {msg.content}")

    _add_messages("前文消息", request.before_messages)
    _add_messages("@AI消息", [request.at_message])
    _add_messages("后续消息", request.after_messages)

    return "\n".join(lines)


def analyze_intent(request: AnalyzeRequest) -> dict:
    """调用 LLM 分析意图，根据是否有图片选择不同模型。"""
    use_vision = _has_image(request)

    if use_vision:
        client = OpenAI(base_url=settings.vision_llm_base_url, api_key=settings.vision_llm_api_key)
        model = settings.vision_llm_model
        user_content = _build_user_content(request)
    else:
        client = OpenAI(base_url=settings.text_llm_base_url, api_key=settings.text_llm_api_key)
        model = settings.text_llm_model
        user_content = _build_user_text(request)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    return json.loads(raw)
