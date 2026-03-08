from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Message(BaseModel):
    type: str  # "text" or "image"
    content: Optional[str] = None  # 文本内容
    url: Optional[str] = None  # 图片 URL


class AnalyzeRequest(BaseModel):
    before_messages: list[Message] = []
    at_message: Message
    after_messages: list[Message] = []


class AnalyzeResponse(BaseModel):
    intent: str  # "query_logistics" | "query_inventory" | "not_sure_intent"
    status: Optional[str] = None  # "success" | "no_tracking_no" | "no_info_extracted"
    order_no: Optional[str] = None
    item_code: Optional[str] = None
    item_name: Optional[str] = None
