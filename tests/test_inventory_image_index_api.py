"""图片检索库异步入库接口与百度索引逻辑测试。"""

import time

import pytest
from fastapi.testclient import TestClient

from app.image_index import (
    BaiduImageIndexer,
    ImageIndexError,
    ImageIndexItem,
    InventoryImageIndexTaskService,
    SQLiteImageIndexMappingStore,
)
from app.main import app


class FakeImageIndexer:
    """用于 HTTP 接口测试的假索引器。"""

    def __init__(self, failures=None, delay: float = 0):
        self.failures = failures or {}
        self.delay = delay
        self.calls = []

    def upsert_image(self, item: ImageIndexItem) -> None:
        """记录调用，并按测试配置决定是否抛错。"""
        self.calls.append({
            "code": item.code,
            "name": item.name,
            "image_type": item.image_type,
            "image_url": item.image_url,
        })
        if self.delay:
            time.sleep(self.delay)

        failure = self.failures.get((item.code, item.image_type))
        if failure:
            raise ImageIndexError(failure["error_code"], failure["error_message"])


class FakeBaiduClient:
    """用于索引器测试的假百度客户端。"""

    def __init__(self, add_signs=None, delete_failures=None):
        self._add_signs = list(add_signs or [])
        self._delete_failures = set(delete_failures or [])
        self.add_calls = []
        self.update_calls = []
        self.delete_calls = []

    def product_add(self, image_url: str, brief: str) -> str:
        """记录新增图片调用，并返回预设的 cont_sign。"""
        cont_sign = self._add_signs.pop(0) if self._add_signs else f"cont_sign_{len(self.add_calls) + 1}"
        self.add_calls.append({
            "image_url": image_url,
            "brief": brief,
            "cont_sign": cont_sign,
        })
        return cont_sign

    def product_update(self, cont_sign: str, brief: str) -> None:
        """记录摘要更新调用。"""
        self.update_calls.append({
            "cont_sign": cont_sign,
            "brief": brief,
        })

    def product_delete_by_sign(self, cont_signs: list[str]) -> None:
        """记录删除调用，并按预设控制失败。"""
        self.delete_calls.append(list(cont_signs))
        if any(cont_sign in self._delete_failures for cont_sign in cont_signs):
            raise ImageIndexError("IMAGE_DELETE_FAILED", "删除百度图片失败")


def _build_client(monkeypatch, indexer: FakeImageIndexer) -> TestClient:
    """为每个测试注入独立任务服务，避免彼此串数据。"""
    service = InventoryImageIndexTaskService(indexer=indexer)
    monkeypatch.setattr("app.main.image_index_task_service", service)
    return TestClient(app)


def _wait_for_task_finished(client: TestClient, task_id: str, timeout_seconds: float = 2) -> dict:
    """轮询任务直到进入终态，避免后台线程尚未跑完。"""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        response = client.get(f"/inventory_image_index/tasks/{task_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"success", "partial_success", "failed"}:
            return body
        time.sleep(0.02)
    raise AssertionError("任务在预期时间内没有结束")


def test_create_inventory_image_index_task_success(monkeypatch):
    """成功场景应按图片维度统计并返回完成结果。"""
    client = _build_client(monkeypatch, FakeImageIndexer())

    response = client.post(
        "/inventory_image_index/tasks",
        json={
            "products": [
                {
                    "code": "01028",
                    "name": "宝可梦睡姿明盒",
                    "picture_url": "https://example.com/01028-product.jpg",
                    "small_package_picture_url": "https://example.com/01028-small.jpg",
                    "middle_package_picture_url": None,
                },
                {
                    "code": "0102250",
                    "name": "宝可梦立牌",
                    "picture_url": "",
                    "small_package_picture_url": "https://example.com/0102250-small.jpg",
                    "middle_package_picture_url": "https://example.com/0102250-middle.jpg",
                },
            ]
        },
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "pending"
    assert body["total_product_count"] == 2
    assert body["submitted_image_count"] == 4
    assert body["ignored_empty_image_count"] == 2

    detail = _wait_for_task_finished(client, body["task_id"])
    assert detail["status"] == "success"
    assert detail["processed_image_count"] == 4
    assert detail["succeeded_image_count"] == 4
    assert detail["failed_image_count"] == 0
    assert detail["pending_image_count"] == 0
    assert detail["failed_items"] == []


def test_create_inventory_image_index_task_partial_success(monkeypatch):
    """部分图片失败时，应返回失败计数和失败明细。"""
    client = _build_client(
        monkeypatch,
        FakeImageIndexer(
            failures={
                ("0102250", "middle_package"): {
                    "error_code": "IMAGE_UPLOAD_FAILED",
                    "error_message": "百度图片入库失败",
                }
            }
        ),
    )

    response = client.post(
        "/inventory_image_index/tasks",
        json={
            "products": [
                {
                    "code": "0102250",
                    "name": "宝可梦立牌",
                    "picture_url": "https://example.com/0102250-product.jpg",
                    "small_package_picture_url": None,
                    "middle_package_picture_url": "https://example.com/0102250-middle.jpg",
                }
            ]
        },
    )

    assert response.status_code == 202
    detail = _wait_for_task_finished(client, response.json()["task_id"])
    assert detail["status"] == "partial_success"
    assert detail["submitted_image_count"] == 2
    assert detail["processed_image_count"] == 2
    assert detail["succeeded_image_count"] == 1
    assert detail["failed_image_count"] == 1
    assert detail["pending_image_count"] == 0
    assert detail["failed_items"] == [
        {
            "code": "0102250",
            "name": "宝可梦立牌",
            "image_type": "middle_package",
            "image_url": "https://example.com/0102250-middle.jpg",
            "error_code": "IMAGE_UPLOAD_FAILED",
            "error_message": "百度图片入库失败",
        }
    ]


def test_create_inventory_image_index_task_deduplicates_by_code_and_image_type(monkeypatch):
    """同一批次里重复的图片键应以后出现的非空记录覆盖。"""
    indexer = FakeImageIndexer()
    client = _build_client(monkeypatch, indexer)

    response = client.post(
        "/inventory_image_index/tasks",
        json={
            "products": [
                {
                    "code": "01028",
                    "name": "旧名称",
                    "picture_url": "https://example.com/old.jpg",
                    "small_package_picture_url": None,
                    "middle_package_picture_url": None,
                },
                {
                    "code": "01028",
                    "name": "新名称",
                    "picture_url": "https://example.com/new.jpg",
                    "small_package_picture_url": None,
                    "middle_package_picture_url": None,
                },
            ]
        },
    )

    assert response.status_code == 202
    detail = _wait_for_task_finished(client, response.json()["task_id"])
    assert detail["submitted_image_count"] == 1
    assert detail["succeeded_image_count"] == 1
    assert indexer.calls == [
        {
            "code": "01028",
            "name": "新名称",
            "image_type": "product",
            "image_url": "https://example.com/new.jpg",
        }
    ]


def test_get_inventory_image_index_task_not_found(monkeypatch):
    """不存在的任务应返回 404。"""
    client = _build_client(monkeypatch, FakeImageIndexer())

    response = client.get("/inventory_image_index/tasks/not_found")

    assert response.status_code == 404
    assert response.json() == {"error": "task_id 不存在"}


def test_create_inventory_image_index_task_requires_products(monkeypatch):
    """空商品数组应直接返回 400。"""
    client = _build_client(monkeypatch, FakeImageIndexer())

    response = client.post("/inventory_image_index/tasks", json={"products": []})

    assert response.status_code == 400
    assert response.json() == {"error": "products 不能为空"}


@pytest.fixture
def mapping_store(tmp_path):
    """为索引器测试提供独立的 SQLite 映射文件。"""
    return SQLiteImageIndexMappingStore(str(tmp_path / "baidu_image_index.sqlite3"))


def test_baidu_indexer_updates_metadata_when_same_url_changes_name(mapping_store):
    """同一图片地址只更新名称时，应走百度 update 而不是重新 add。"""
    client = FakeBaiduClient(add_signs=["sign_old"])
    indexer = BaiduImageIndexer(client=client, mapping_store=mapping_store)

    indexer.upsert_image(ImageIndexItem("01028", "旧名称", "product", "https://example.com/old.jpg"))
    indexer.upsert_image(ImageIndexItem("01028", "新名称", "product", "https://example.com/old.jpg"))

    mapping = mapping_store.get_mapping("01028", "product")
    assert mapping is not None
    assert mapping.name == "新名称"
    assert mapping.source_url == "https://example.com/old.jpg"
    assert mapping.cont_sign == "sign_old"
    assert client.add_calls == [
        {
            "image_url": "https://example.com/old.jpg",
            "brief": "{\"code\":\"01028\",\"name\":\"旧名称\",\"image_type\":\"product\"}",
            "cont_sign": "sign_old",
        }
    ]
    assert client.update_calls == [
        {
            "cont_sign": "sign_old",
            "brief": "{\"code\":\"01028\",\"name\":\"新名称\",\"image_type\":\"product\"}",
        }
    ]
    assert client.delete_calls == []


def test_baidu_indexer_replaces_old_image_when_source_url_changes(mapping_store):
    """图片地址变化时，应新增新图、删除旧图，并更新本地映射。"""
    client = FakeBaiduClient(add_signs=["sign_old", "sign_new"])
    indexer = BaiduImageIndexer(client=client, mapping_store=mapping_store)

    indexer.upsert_image(ImageIndexItem("01028", "旧名称", "product", "https://example.com/old.jpg"))
    indexer.upsert_image(ImageIndexItem("01028", "新名称", "product", "https://example.com/new.jpg"))

    mapping = mapping_store.get_mapping("01028", "product")
    assert mapping is not None
    assert mapping.name == "新名称"
    assert mapping.source_url == "https://example.com/new.jpg"
    assert mapping.cont_sign == "sign_new"
    assert client.add_calls == [
        {
            "image_url": "https://example.com/old.jpg",
            "brief": "{\"code\":\"01028\",\"name\":\"旧名称\",\"image_type\":\"product\"}",
            "cont_sign": "sign_old",
        },
        {
            "image_url": "https://example.com/new.jpg",
            "brief": "{\"code\":\"01028\",\"name\":\"新名称\",\"image_type\":\"product\"}",
            "cont_sign": "sign_new",
        },
    ]
    assert client.update_calls == []
    assert client.delete_calls == [["sign_old"]]


def test_baidu_indexer_rolls_back_new_image_when_delete_old_fails(mapping_store):
    """旧图删除失败时，应回滚新图并保留旧映射。"""
    client = FakeBaiduClient(add_signs=["sign_old", "sign_new"], delete_failures={"sign_old"})
    indexer = BaiduImageIndexer(client=client, mapping_store=mapping_store)

    indexer.upsert_image(ImageIndexItem("01028", "旧名称", "product", "https://example.com/old.jpg"))

    with pytest.raises(ImageIndexError) as exc_info:
        indexer.upsert_image(ImageIndexItem("01028", "新名称", "product", "https://example.com/new.jpg"))

    mapping = mapping_store.get_mapping("01028", "product")
    assert mapping is not None
    assert mapping.name == "旧名称"
    assert mapping.source_url == "https://example.com/old.jpg"
    assert mapping.cont_sign == "sign_old"
    assert exc_info.value.code == "IMAGE_DELETE_FAILED"
    assert client.delete_calls == [["sign_old"], ["sign_new"]]


def test_baidu_indexer_reuses_mapping_across_store_instances(tmp_path):
    """同一个 SQLite 文件应让不同索引器实例共享映射。"""
    db_path = str(tmp_path / "baidu_image_index.sqlite3")
    first_store = SQLiteImageIndexMappingStore(db_path)
    second_store = SQLiteImageIndexMappingStore(db_path)
    first_client = FakeBaiduClient(add_signs=["sign_old"])
    second_client = FakeBaiduClient()
    first_indexer = BaiduImageIndexer(client=first_client, mapping_store=first_store)
    second_indexer = BaiduImageIndexer(client=second_client, mapping_store=second_store)

    first_indexer.upsert_image(ImageIndexItem("01028", "旧名称", "product", "https://example.com/old.jpg"))
    second_indexer.upsert_image(ImageIndexItem("01028", "旧名称", "product", "https://example.com/old.jpg"))

    mapping = second_store.get_mapping("01028", "product")
    assert mapping is not None
    assert mapping.cont_sign == "sign_old"
    assert len(first_client.add_calls) == 1
    assert second_client.add_calls == []
    assert second_client.update_calls == []
    assert second_client.delete_calls == []
