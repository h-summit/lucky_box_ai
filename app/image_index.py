import json
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from threading import Lock, Thread
from typing import Protocol
from uuid import uuid4

import httpx

from app.config import settings
from app.schemas import InventoryImageIndexProduct

IMAGE_FIELD_TO_TYPE = {
    "picture_url": "product",
    "small_package_picture_url": "small_package",
    "middle_package_picture_url": "middle_package",
}


class ImageIndexError(Exception):
    """统一封装图片入库过程中的业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ImageUploader(Protocol):
    """图片上传器协议，便于在测试里替换真实实现。"""

    def add_image(self, code: str, name: str, image_type: str, image_url: str) -> None:
        """将单张图片写入图片检索库。"""


@dataclass
class ImageIndexItem:
    """任务里实际待处理的一张图片。"""

    code: str
    name: str
    image_type: str
    image_url: str


@dataclass
class ImageIndexFailedItem:
    """任务里的失败图片记录。"""

    code: str
    name: str
    image_type: str
    image_url: str
    error_code: str
    error_message: str


@dataclass
class ImageIndexTask:
    """异步入库任务的进程内状态。"""

    task_id: str
    total_product_count: int
    submitted_image_count: int
    ignored_empty_image_count: int
    created_at: datetime
    items: list[ImageIndexItem] = field(default_factory=list)
    status: str = "pending"
    started_at: datetime | None = None
    finished_at: datetime | None = None
    succeeded_image_count: int = 0
    failed_image_count: int = 0
    failed_items: list[ImageIndexFailedItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        """将任务状态转换为接口响应。"""
        processed_image_count = self.succeeded_image_count + self.failed_image_count
        return {
            "task_id": self.task_id,
            "status": self.status,
            "total_product_count": self.total_product_count,
            "submitted_image_count": self.submitted_image_count,
            "processed_image_count": processed_image_count,
            "succeeded_image_count": self.succeeded_image_count,
            "failed_image_count": self.failed_image_count,
            "pending_image_count": self.submitted_image_count - processed_image_count,
            "ignored_empty_image_count": self.ignored_empty_image_count,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "failed_items": [
                {
                    "code": item.code,
                    "name": item.name,
                    "image_type": item.image_type,
                    "image_url": item.image_url,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in self.failed_items
            ],
        }


class AliyunImageSearchUploader:
    """阿里云图像搜索上传器。"""

    def add_image(self, code: str, name: str, image_type: str, image_url: str) -> None:
        """下载图片并调用阿里云图像搜索入库。"""
        self._validate_settings()
        content = self._download_image(image_url)
        buffer = BytesIO(content)
        try:
            request = self._build_request(code=code, name=name, image_type=image_type, buffer=buffer)
            client = self._build_client()
            response = client.add_image_advance(request)
        except ModuleNotFoundError as exc:
            raise ImageIndexError("SDK_NOT_INSTALLED", f"阿里云图像搜索 SDK 未安装: {exc}") from exc
        except Exception as exc:  # pragma: no cover - 真实 SDK 异常类型依赖运行环境
            raise ImageIndexError("ALIYUN_ADD_IMAGE_FAILED", f"阿里云图片入库失败: {exc}") from exc
        finally:
            buffer.close()

        payload = self._extract_payload(response)
        if payload.get("Success") is not True:
            message = payload.get("Message") or payload.get("Code") or "阿里云图片入库失败"
            raise ImageIndexError("ALIYUN_ADD_IMAGE_FAILED", str(message))

    def _validate_settings(self) -> None:
        """校验阿里云图像搜索的最小配置。"""
        missing_fields = [
            field_name
            for field_name, value in {
                "aliyun_image_search_access_key_id": settings.aliyun_image_search_access_key_id,
                "aliyun_image_search_access_key_secret": settings.aliyun_image_search_access_key_secret,
                "aliyun_image_search_instance_name": settings.aliyun_image_search_instance_name,
            }.items()
            if not value
        ]
        if not (settings.aliyun_image_search_endpoint or settings.aliyun_image_search_region_id):
            missing_fields.append("aliyun_image_search_endpoint")

        if missing_fields:
            raise ImageIndexError(
                "SETTINGS_MISSING",
                f"缺少阿里云图像搜索配置: {', '.join(missing_fields)}",
            )

    def _download_image(self, image_url: str) -> bytes:
        """先把远端图片下载到内存，再交给阿里云 SDK。"""
        try:
            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.get(image_url)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ImageIndexError("IMAGE_DOWNLOAD_FAILED", f"图片下载失败: {exc}") from exc

        if not response.content:
            raise ImageIndexError("IMAGE_DOWNLOAD_FAILED", "图片下载失败: 响应内容为空")
        return response.content

    def _build_request(self, code: str, name: str, image_type: str, buffer: BytesIO):
        """构造阿里云新增图片请求。"""
        from alibabacloud_imagesearch20201214 import models as imagesearch_models

        return imagesearch_models.AddImageAdvanceRequest(
            instance_name=settings.aliyun_image_search_instance_name,
            product_id=code,
            pic_name=image_type,
            pic_content_object=buffer,
            custom_content=json.dumps(
                {"code": code, "name": name, "image_type": image_type},
                ensure_ascii=False,
            ),
        )

    def _build_client(self):
        """按当前配置创建阿里云图像搜索客户端。"""
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_imagesearch20201214.client import Client as ImageSearchClient

        endpoint = settings.aliyun_image_search_endpoint
        if not endpoint:
            endpoint = f"imagesearch.{settings.aliyun_image_search_region_id}.aliyuncs.com"

        config = open_api_models.Config(
            access_key_id=settings.aliyun_image_search_access_key_id,
            access_key_secret=settings.aliyun_image_search_access_key_secret,
            endpoint=endpoint,
        )
        return ImageSearchClient(config)

    def _extract_payload(self, response) -> dict:
        """兼容 SDK 不同返回对象形态，只抽取业务 body。"""
        if hasattr(response, "body") and hasattr(response.body, "to_map"):
            return response.body.to_map()
        if hasattr(response, "to_map"):
            payload = response.to_map()
            if isinstance(payload.get("body"), dict):
                return payload["body"]
            return payload
        return {}


class InventoryImageIndexTaskService:
    """异步图片入库任务服务。"""

    def __init__(self, uploader: ImageUploader | None = None):
        """默认使用阿里云上传器，也允许测试时注入假实现。"""
        self._uploader = uploader or AliyunImageSearchUploader()
        self._tasks: dict[str, ImageIndexTask] = {}
        self._lock = Lock()

    def create_task(self, products: list[InventoryImageIndexProduct]) -> dict:
        """创建任务并保存初始状态。"""
        items, ignored_empty_image_count = self._build_items(products)
        task = ImageIndexTask(
            task_id=self._generate_task_id(),
            total_product_count=len(products),
            submitted_image_count=len(items),
            ignored_empty_image_count=ignored_empty_image_count,
            created_at=datetime.now().astimezone(),
            items=items,
        )
        with self._lock:
            self._tasks[task.task_id] = task
        return task.to_dict()

    def start_task(self, task_id: str) -> None:
        """启动后台线程处理任务。"""
        worker = Thread(target=self._run_task, args=(task_id,), daemon=True)
        worker.start()

    def get_task(self, task_id: str) -> dict | None:
        """读取任务快照。"""
        with self._lock:
            task = self._tasks.get(task_id)
            return task.to_dict() if task else None

    def _run_task(self, task_id: str) -> None:
        """按图片顺序执行入库，并实时更新任务状态。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "running"
            task.started_at = datetime.now().astimezone()

        if not task.items:
            with self._lock:
                task.status = "success"
                task.finished_at = datetime.now().astimezone()
            return

        for item in task.items:
            try:
                self._uploader.add_image(
                    code=item.code,
                    name=item.name,
                    image_type=item.image_type,
                    image_url=item.image_url,
                )
            except ImageIndexError as exc:
                self._mark_failed(task_id=task_id, item=item, error_code=exc.code, error_message=exc.message)
                continue
            except Exception as exc:  # pragma: no cover - 兜底分支只在非预期异常时触发
                self._mark_failed(task_id=task_id, item=item, error_code="UNEXPECTED_ERROR", error_message=str(exc))
                continue

            self._mark_succeeded(task_id)

        with self._lock:
            current_task = self._tasks.get(task_id)
            if current_task is None:
                return
            current_task.finished_at = datetime.now().astimezone()
            current_task.status = self._resolve_final_status(current_task)

    def _build_items(self, products: list[InventoryImageIndexProduct]) -> tuple[list[ImageIndexItem], int]:
        """把商品展开成图片记录，并在批次内按唯一键去重。"""
        deduplicated_items: dict[tuple[str, str], ImageIndexItem] = {}
        ignored_empty_image_count = 0

        for product in products:
            for field_name, image_type in IMAGE_FIELD_TO_TYPE.items():
                image_url = getattr(product, field_name)
                if not image_url:
                    ignored_empty_image_count += 1
                    continue
                deduplicated_items[(product.code, image_type)] = ImageIndexItem(
                    code=product.code,
                    name=product.name,
                    image_type=image_type,
                    image_url=image_url,
                )

        return list(deduplicated_items.values()), ignored_empty_image_count

    def _mark_succeeded(self, task_id: str) -> None:
        """成功处理一张图片后递增统计。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.succeeded_image_count += 1

    def _mark_failed(self, task_id: str, item: ImageIndexItem, error_code: str, error_message: str) -> None:
        """失败时记录明细并递增失败数。"""
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.failed_image_count += 1
            task.failed_items.append(
                ImageIndexFailedItem(
                    code=item.code,
                    name=item.name,
                    image_type=item.image_type,
                    image_url=item.image_url,
                    error_code=error_code,
                    error_message=error_message,
                )
            )

    def _resolve_final_status(self, task: ImageIndexTask) -> str:
        """根据成功/失败计数得到最终任务状态。"""
        if task.failed_image_count == 0:
            return "success"
        if task.succeeded_image_count == 0:
            return "failed"
        return "partial_success"

    def _generate_task_id(self) -> str:
        """生成可读且足够唯一的任务 ID。"""
        return f"imgidx_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
