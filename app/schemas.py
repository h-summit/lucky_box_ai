from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


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
    intent: str  # "query_logistics" | "query_inventory" | "get_quote" | "not_sure_intent"
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


class InventoryImageIndexProduct(BaseModel):
    """图片检索库入库的单个商品。"""

    code: str
    name: str
    picture_url: Optional[str] = None
    small_package_picture_url: Optional[str] = None
    middle_package_picture_url: Optional[str] = None


class InventoryImageIndexTaskCreateRequest(BaseModel):
    """提交图片检索库异步入库任务的请求体。"""

    products: list[InventoryImageIndexProduct] = Field(default_factory=list)


class InventoryImageIndexTaskCreateResponse(BaseModel):
    """创建异步入库任务后的响应。"""

    task_id: str
    status: str
    total_product_count: int
    submitted_image_count: int
    ignored_empty_image_count: int
    created_at: str


class InventoryImageIndexTaskFailedItem(BaseModel):
    """单张图片入库失败明细。"""

    code: str
    name: str
    image_type: str
    image_url: str
    error_code: str
    error_message: str


class InventoryImageIndexTaskDetailResponse(BaseModel):
    """异步入库任务的进度与结果。"""

    task_id: str
    status: str
    total_product_count: int
    submitted_image_count: int
    processed_image_count: int
    succeeded_image_count: int
    failed_image_count: int
    pending_image_count: int
    ignored_empty_image_count: int
    created_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    failed_items: list[InventoryImageIndexTaskFailedItem] = Field(default_factory=list)
