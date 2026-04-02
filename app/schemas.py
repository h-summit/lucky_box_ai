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


class InventoryItem(BaseModel):
    item_code: Optional[str] = None
    item_name: Optional[str] = None


class AnalyzeResponse(BaseModel):
    intent: str  # "query_logistics" | "query_inventory" | "not_sure_intent"
    status: Optional[str] = None  # "success" | "no_tracking_no" | "no_info_extracted"
    order_no: Optional[str] = None
    items: Optional[list[InventoryItem]] = None


class GreetingsRequest(BaseModel):
    prompt: str
    product_info: str


class HistoryMessage(BaseModel):
    role: str
    content: str


class HolidayGreetingsRequest(BaseModel):
    holiday: str
    time_now: str
    history: list[HistoryMessage] = []


class CustomerRelationshipManagementRequest(BaseModel):
    time_delay: str
    time_now: str
    history: list[HistoryMessage] = []


class ReplyResponse(BaseModel):
    response: str
