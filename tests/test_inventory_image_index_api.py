"""图片检索库异步入库接口测试。"""

import time

from fastapi.testclient import TestClient

from app.image_index import ImageIndexError, InventoryImageIndexTaskService
from app.main import app


class FakeImageUploader:
    """用可控的假上传器替代真实阿里云调用。"""

    def __init__(self, failures=None, delay: float = 0):
        self.failures = failures or {}
        self.delay = delay
        self.calls = []

    def add_image(self, code: str, name: str, image_type: str, image_url: str) -> None:
        """记录调用，并按测试配置决定是否抛错。"""
        self.calls.append({
            "code": code,
            "name": name,
            "image_type": image_type,
            "image_url": image_url,
        })
        if self.delay:
            time.sleep(self.delay)

        failure = self.failures.get((code, image_type))
        if failure:
            raise ImageIndexError(failure["error_code"], failure["error_message"])


def _build_client(monkeypatch, uploader: FakeImageUploader) -> TestClient:
    """为每个测试注入独立的任务服务，避免彼此串数据。"""
    service = InventoryImageIndexTaskService(uploader=uploader)
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
    client = _build_client(monkeypatch, FakeImageUploader())

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
    uploader = FakeImageUploader(
        failures={
            ("0102250", "middle_package"): {
                "error_code": "ALIYUN_ADD_IMAGE_FAILED",
                "error_message": "阿里云图片入库失败",
            }
        }
    )
    client = _build_client(monkeypatch, uploader)

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
            "error_code": "ALIYUN_ADD_IMAGE_FAILED",
            "error_message": "阿里云图片入库失败",
        }
    ]


def test_create_inventory_image_index_task_deduplicates_by_code_and_image_type(monkeypatch):
    """同一批次里重复的图片键应以后出现的非空记录覆盖。"""
    uploader = FakeImageUploader()
    client = _build_client(monkeypatch, uploader)

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
    assert uploader.calls == [
        {
            "code": "01028",
            "name": "新名称",
            "image_type": "product",
            "image_url": "https://example.com/new.jpg",
        }
    ]


def test_get_inventory_image_index_task_not_found(monkeypatch):
    """不存在的任务应返回 404。"""
    client = _build_client(monkeypatch, FakeImageUploader())

    response = client.get("/inventory_image_index/tasks/not_found")

    assert response.status_code == 404
    assert response.json() == {"error": "task_id 不存在"}


def test_create_inventory_image_index_task_requires_products(monkeypatch):
    """空商品数组应直接返回 400。"""
    client = _build_client(monkeypatch, FakeImageUploader())

    response = client.post("/inventory_image_index/tasks", json={"products": []})

    assert response.status_code == 400
    assert response.json() == {"error": "products 不能为空"}
