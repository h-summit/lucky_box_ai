import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
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
AUTH_ERROR_CODES = {6, 13, 14, 15, 100, 110, 111}
IMAGE_SEARCH_SCORE_THRESHOLD = 0.85
IMAGE_SEARCH_TOP_N = 3


class ImageIndexError(Exception):
    """统一封装图片入库过程中的业务错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


@dataclass
class ImageIndexMapping:
    """本地保存的百度图片映射。"""

    code: str
    name: str
    image_type: str
    source_url: str
    brief: str
    cont_sign: str
    updated_at: str


@dataclass
class BaiduProductSearchHit:
    """百度商品检索返回的一条候选结果。"""

    score: float
    brief: str
    cont_sign: str = ""


class ImageIndexMappingStore(Protocol):
    """图片映射存储协议，便于测试时替换实现。"""

    def get_mapping(self, code: str, image_type: str) -> ImageIndexMapping | None:
        """按业务主键读取当前映射。"""

    def upsert_mapping(self, mapping: ImageIndexMapping) -> None:
        """保存或覆盖当前映射。"""


class BaiduImageSearchApi(Protocol):
    """百度图片搜索客户端协议，便于测试时替换实现。"""

    def product_add(self, image_url: str, brief: str) -> str:
        """按图片 URL 新增一张图片，返回百度生成的 cont_sign。"""

    def product_update(self, cont_sign: str, brief: str) -> None:
        """更新已入库图片的摘要信息。"""

    def product_delete_by_sign(self, cont_signs: list[str]) -> None:
        """按 cont_sign 删除图片。"""

    def product_search(self, image_ref: str, rn: int) -> list[BaiduProductSearchHit]:
        """按图片检索相似商品。"""


class ImageIndexer(Protocol):
    """图片业务索引协议，任务服务只依赖这个最小接口。"""

    def upsert_image(self, item: ImageIndexItem) -> None:
        """按业务主键执行图片同步。"""


def _build_search_payload(image_ref: str, rn: int) -> dict[str, str]:
    """统一构造百度搜图请求参数，兼容公网 URL 和 data URL。"""
    payload = {"rn": str(rn)}
    if image_ref.startswith("data:"):
        comma_index = image_ref.find(",")
        if comma_index == -1:
            raise ImageIndexError("IMAGE_SEARCH_FAILED", "图片检索失败: data URL 格式不合法")
        payload["image"] = image_ref[comma_index + 1:]
        return payload

    payload["url"] = image_ref
    return payload


def _build_brief(code: str, name: str, image_type: str) -> str:
    """统一生成百度检索时需要回传的业务摘要。"""
    return json.dumps(
        {"code": code, "name": name, "image_type": image_type},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _now_iso() -> str:
    """统一生成本地映射的更新时间。"""
    return datetime.now().astimezone().isoformat()


class SQLiteImageIndexMappingStore:
    """用 SQLite 持久化保存百度 cont_sign 映射。"""

    def __init__(self, db_path: str | None = None):
        """默认使用配置中的路径，也允许测试注入临时文件。"""
        self._db_path = Path(db_path or settings.baidu_image_search_mapping_db_path)
        self._lock = Lock()
        self._initialized = False

    def get_mapping(self, code: str, image_type: str) -> ImageIndexMapping | None:
        """按业务主键读取当前映射。"""
        self._ensure_initialized()
        try:
            with self._lock, sqlite3.connect(self._db_path, timeout=30) as conn:
                row = conn.execute(
                    """
                    SELECT code, name, image_type, source_url, brief, cont_sign, updated_at
                    FROM baidu_image_index_mappings
                    WHERE code = ? AND image_type = ?
                    """,
                    (code, image_type),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ImageIndexError("MAPPING_STORE_FAILED", f"读取图片映射失败: {exc}") from exc

        if row is None:
            return None
        return ImageIndexMapping(
            code=row[0],
            name=row[1],
            image_type=row[2],
            source_url=row[3],
            brief=row[4],
            cont_sign=row[5],
            updated_at=row[6],
        )

    def upsert_mapping(self, mapping: ImageIndexMapping) -> None:
        """保存或覆盖当前映射。"""
        self._ensure_initialized()
        try:
            with self._lock, sqlite3.connect(self._db_path, timeout=30) as conn:
                conn.execute(
                    """
                    INSERT INTO baidu_image_index_mappings (
                        code, name, image_type, source_url, brief, cont_sign, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(code, image_type) DO UPDATE SET
                        name = excluded.name,
                        source_url = excluded.source_url,
                        brief = excluded.brief,
                        cont_sign = excluded.cont_sign,
                        updated_at = excluded.updated_at
                    """,
                    (
                        mapping.code,
                        mapping.name,
                        mapping.image_type,
                        mapping.source_url,
                        mapping.brief,
                        mapping.cont_sign,
                        mapping.updated_at,
                    ),
                )
                conn.commit()
        except sqlite3.Error as exc:
            raise ImageIndexError("MAPPING_STORE_FAILED", f"保存图片映射失败: {exc}") from exc

    def _ensure_initialized(self) -> None:
        """懒创建数据库和表，避免模块导入阶段就触发副作用。"""
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                with sqlite3.connect(self._db_path, timeout=30) as conn:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS baidu_image_index_mappings (
                            code TEXT NOT NULL,
                            name TEXT NOT NULL,
                            image_type TEXT NOT NULL,
                            source_url TEXT NOT NULL,
                            brief TEXT NOT NULL,
                            cont_sign TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            PRIMARY KEY (code, image_type)
                        )
                        """
                    )
                    conn.commit()
            except sqlite3.Error as exc:
                raise ImageIndexError("MAPPING_STORE_FAILED", f"初始化图片映射库失败: {exc}") from exc
            self._initialized = True


class BaiduImageSearchClient:
    """封装百度商品图片搜索的最小 API 集。"""

    def __init__(
        self,
        api_key: str | None = None,
        secret_key: str | None = None,
        base_url: str | None = None,
        transport=None,
    ):
        """默认读取配置，也允许测试注入 MockTransport。"""
        self._api_key = api_key or settings.baidu_image_search_api_key
        self._secret_key = secret_key or settings.baidu_image_search_secret_key
        self._base_url = (base_url or settings.baidu_image_search_base_url).rstrip("/")
        self._transport = transport
        self._token_lock = Lock()
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def product_add(self, image_url: str, brief: str) -> str:
        """按图片 URL 新增一张图片并返回 cont_sign。"""
        payload = self._call_api(
            "/rest/2.0/image-classify/v1/realtime_search/product/add",
            data={
                "url": image_url,
                "brief": brief,
            },
            failure_code="IMAGE_UPLOAD_FAILED",
        )
        cont_sign = payload.get("cont_sign")
        if not cont_sign:
            raise ImageIndexError("IMAGE_UPLOAD_FAILED", "百度图片入库失败: 未返回 cont_sign")
        return cont_sign

    def product_update(self, cont_sign: str, brief: str) -> None:
        """按 cont_sign 更新百度图库里的摘要信息。"""
        self._call_api(
            "/rest/2.0/image-classify/v1/realtime_search/product/update",
            data={
                "cont_sign": cont_sign,
                "brief": brief,
            },
            failure_code="IMAGE_METADATA_UPDATE_FAILED",
        )

    def product_delete_by_sign(self, cont_signs: list[str]) -> None:
        """按 cont_sign 删除百度图库中的图片。"""
        self._call_api(
            "/rest/2.0/image-classify/v1/realtime_search/product/delete",
            data={"cont_sign": ",".join(cont_signs)},
            failure_code="IMAGE_DELETE_FAILED",
        )

    def product_search(self, image_ref: str, rn: int) -> list[BaiduProductSearchHit]:
        """按图片检索相似商品候选。"""
        payload = self._call_api(
            "/rest/2.0/image-classify/v1/realtime_search/product/search",
            data=_build_search_payload(image_ref, rn),
            failure_code="IMAGE_SEARCH_FAILED",
        )

        raw_results = payload.get("result")
        if not isinstance(raw_results, list):
            return []

        hits = []
        for raw_result in raw_results:
            if not isinstance(raw_result, dict):
                continue
            try:
                score = float(raw_result.get("score", 0))
            except (TypeError, ValueError):
                continue

            brief = raw_result.get("brief")
            if not isinstance(brief, str):
                continue

            hits.append(BaiduProductSearchHit(
                score=score,
                brief=brief,
                cont_sign=str(raw_result.get("cont_sign") or ""),
            ))

        return hits

    def _call_api(self, path: str, data: dict[str, str], failure_code: str, retry_auth: bool = True) -> dict:
        """统一处理 token、HTTP 请求和百度错误码。"""
        token = self._get_access_token(force_refresh=False)
        try:
            with self._build_http_client() as client:
                response = client.post(
                    path,
                    params={"access_token": token},
                    data=data,
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            raise ImageIndexError(failure_code, f"百度图片搜索请求失败: {exc}") from exc
        except ValueError as exc:
            raise ImageIndexError(failure_code, "百度图片搜索返回了非 JSON 数据") from exc

        error_code = payload.get("error_code")
        if error_code is None:
            return payload

        try:
            numeric_error_code = int(error_code)
        except (TypeError, ValueError):
            numeric_error_code = None

        if numeric_error_code in AUTH_ERROR_CODES and retry_auth:
            self._get_access_token(force_refresh=True)
            return self._call_api(path, data=data, failure_code=failure_code, retry_auth=False)

        mapped_code = "AUTH_FAILED" if numeric_error_code in AUTH_ERROR_CODES else failure_code
        error_message = payload.get("error_msg") or "未知错误"
        raise ImageIndexError(mapped_code, f"百度图片搜索调用失败: [{error_code}] {error_message}")

    def _get_access_token(self, force_refresh: bool) -> str:
        """缓存 access_token，避免每张图片都重新换 token。"""
        with self._token_lock:
            if not force_refresh and self._access_token and time.time() < self._access_token_expires_at:
                return self._access_token

            if not self._api_key or not self._secret_key:
                raise ImageIndexError("SETTINGS_MISSING", "缺少百度图片搜索配置: api_key 或 secret_key")

            try:
                with self._build_http_client() as client:
                    response = client.get(
                        "/oauth/2.0/token",
                        params={
                            "grant_type": "client_credentials",
                            "client_id": self._api_key,
                            "client_secret": self._secret_key,
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except httpx.HTTPError as exc:
                raise ImageIndexError("AUTH_FAILED", f"获取百度 access_token 失败: {exc}") from exc
            except ValueError as exc:
                raise ImageIndexError("AUTH_FAILED", "百度 access_token 接口返回了非 JSON 数据") from exc

            access_token = payload.get("access_token")
            expires_in = payload.get("expires_in")
            if not access_token or not expires_in:
                error_message = payload.get("error_description") or payload.get("error") or "未返回 access_token"
                raise ImageIndexError("AUTH_FAILED", f"获取百度 access_token 失败: {error_message}")

            self._access_token = access_token
            self._access_token_expires_at = time.time() + int(expires_in) - 60
            return self._access_token

    def _build_http_client(self) -> httpx.Client:
        """统一构造 HTTP 客户端，便于测试注入 transport。"""
        client_kwargs = {
            "base_url": self._base_url,
            "timeout": 30.0,
            "follow_redirects": True,
        }
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        return httpx.Client(**client_kwargs)


class BaiduImageIndexer:
    """把业务图片同步到百度图库，并维护本地 cont_sign 映射。"""

    def __init__(
        self,
        client: BaiduImageSearchApi | None = None,
        mapping_store: ImageIndexMappingStore | None = None,
    ):
        """默认使用真实百度客户端和 SQLite 映射表。"""
        self._client = client or BaiduImageSearchClient()
        self._mapping_store = mapping_store or SQLiteImageIndexMappingStore()

    def upsert_image(self, item: ImageIndexItem) -> None:
        """按 `(code, image_type)` 语义同步图片。"""
        brief = _build_brief(item.code, item.name, item.image_type)
        current_mapping = self._mapping_store.get_mapping(item.code, item.image_type)

        if current_mapping is None:
            self._create_mapping(item, brief)
            return

        if current_mapping.source_url == item.image_url:
            if current_mapping.brief == brief:
                return
            self._client.product_update(current_mapping.cont_sign, brief)
            self._mapping_store.upsert_mapping(
                ImageIndexMapping(
                    code=item.code,
                    name=item.name,
                    image_type=item.image_type,
                    source_url=item.image_url,
                    brief=brief,
                    cont_sign=current_mapping.cont_sign,
                    updated_at=_now_iso(),
                )
            )
            return

        self._replace_mapping(item, brief, current_mapping)

    def _create_mapping(self, item: ImageIndexItem, brief: str) -> None:
        """首次入库时直接把图片 URL 交给百度，再保存本地映射。"""
        cont_sign = self._client.product_add(item.image_url, brief)
        try:
            self._mapping_store.upsert_mapping(
                ImageIndexMapping(
                    code=item.code,
                    name=item.name,
                    image_type=item.image_type,
                    source_url=item.image_url,
                    brief=brief,
                    cont_sign=cont_sign,
                    updated_at=_now_iso(),
                )
            )
        except ImageIndexError:
            self._rollback_new_image(cont_sign)
            raise

    def _replace_mapping(self, item: ImageIndexItem, brief: str, current_mapping: ImageIndexMapping) -> None:
        """图片地址变化时，先用新 URL 新增，再删除旧图，最后更新映射。"""
        new_cont_sign = self._client.product_add(item.image_url, brief)

        try:
            self._client.product_delete_by_sign([current_mapping.cont_sign])
        except ImageIndexError as exc:
            rollback_message = self._rollback_new_image(new_cont_sign)
            raise ImageIndexError(
                "IMAGE_DELETE_FAILED",
                f"删除旧图片失败: {exc.message}{rollback_message}",
            ) from exc

        self._mapping_store.upsert_mapping(
            ImageIndexMapping(
                code=item.code,
                name=item.name,
                image_type=item.image_type,
                source_url=item.image_url,
                brief=brief,
                cont_sign=new_cont_sign,
                updated_at=_now_iso(),
            )
        )

    def _rollback_new_image(self, cont_sign: str) -> str:
        """删除刚刚新增的图片，尽量避免本地映射和远端图库漂移。"""
        try:
            self._client.product_delete_by_sign([cont_sign])
        except ImageIndexError as exc:
            return f"; 回滚新增图片失败: {exc.message}"
        return ""


class InventoryImageSearchService:
    """按图片检索商品，并产出可补充到查库存结果里的商品信息。"""

    def __init__(self, client: BaiduImageSearchApi | None = None):
        """默认使用真实百度客户端，也允许测试注入假实现。"""
        self._client = client or BaiduImageSearchClient()

    def search_inventory_items(self, image_refs: list[str]) -> list[dict]:
        """汇总多张图片的高置信度候选，只保留 code 和 name 完整的前 3 个。"""
        best_candidates: dict[str, dict] = {}

        for image_ref in image_refs:
            for hit in self._client.product_search(image_ref, rn=IMAGE_SEARCH_TOP_N):
                candidate = self._build_candidate(hit)
                if candidate is None:
                    continue

                current = best_candidates.get(candidate["item_code"])
                if current is None or candidate["score"] > current["score"]:
                    best_candidates[candidate["item_code"]] = candidate

        ranked_candidates = sorted(
            best_candidates.values(),
            key=lambda item: item["score"],
            reverse=True,
        )
        return [
            {
                "item_code": item["item_code"],
                "item_name": item["item_name"],
            }
            for item in ranked_candidates[:IMAGE_SEARCH_TOP_N]
        ]

    def _build_candidate(self, hit: BaiduProductSearchHit) -> dict | None:
        """把百度候选结果转换成库存商品候选，并执行阈值过滤。"""
        if hit.score < IMAGE_SEARCH_SCORE_THRESHOLD:
            return None

        try:
            brief_data = json.loads(hit.brief)
        except json.JSONDecodeError:
            return None

        if not isinstance(brief_data, dict):
            return None

        code = brief_data.get("code")
        name = brief_data.get("name")
        if not code or not name:
            return None

        return {
            "item_code": code,
            "item_name": name,
            "score": hit.score,
        }


class InventoryImageIndexTaskService:
    """异步图片入库任务服务。"""

    def __init__(self, indexer: ImageIndexer | None = None):
        """默认使用百度实现，也允许测试注入假索引器。"""
        self._indexer = indexer or BaiduImageIndexer()
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
                self._indexer.upsert_image(item)
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
