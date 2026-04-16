"""百度图片搜索客户端测试。"""

from urllib.parse import parse_qs

import httpx
import pytest

from app.image_index import BaiduImageSearchClient, BaiduProductSearchHit, ImageIndexError, InventoryImageSearchService


def test_baidu_client_caches_access_token():
    """同一客户端多次调用时应复用未过期的 access_token。"""
    counters = {
        "token": 0,
        "add": 0,
        "update": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/2.0/token":
            counters["token"] += 1
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/add":
            counters["add"] += 1
            assert request.url.params["access_token"] == "token-1"
            form = parse_qs(request.content.decode("utf-8"))
            assert form["url"] == ["https://example.com/01028.jpg"]
            return httpx.Response(200, json={"cont_sign": "sign-1"})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/update":
            counters["update"] += 1
            assert request.url.params["access_token"] == "token-1"
            return httpx.Response(200, json={"log_id": 1})
        raise AssertionError(f"未预期的请求路径: {request.url.path}")

    client = BaiduImageSearchClient(
        api_key="api-key",
        secret_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    cont_sign = client.product_add("https://example.com/01028.jpg", "{\"code\":\"01028\"}")
    client.product_update(cont_sign, "{\"code\":\"01028\",\"name\":\"新名称\"}")

    assert cont_sign == "sign-1"
    assert counters == {"token": 1, "add": 1, "update": 1}


def test_baidu_client_refreshes_token_when_api_reports_auth_error():
    """百度接口提示 token 失效时，应自动刷新一次再重试。"""
    counters = {
        "token": 0,
        "add": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/2.0/token":
            counters["token"] += 1
            token_value = f"token-{counters['token']}"
            return httpx.Response(200, json={"access_token": token_value, "expires_in": 3600})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/add":
            counters["add"] += 1
            if counters["add"] == 1:
                assert request.url.params["access_token"] == "token-1"
                form = parse_qs(request.content.decode("utf-8"))
                assert form["url"] == ["https://example.com/01028.jpg"]
                return httpx.Response(200, json={"error_code": 110, "error_msg": "Access token invalid"})
            assert request.url.params["access_token"] == "token-2"
            form = parse_qs(request.content.decode("utf-8"))
            assert form["url"] == ["https://example.com/01028.jpg"]
            return httpx.Response(200, json={"cont_sign": "sign-2"})
        raise AssertionError(f"未预期的请求路径: {request.url.path}")

    client = BaiduImageSearchClient(
        api_key="api-key",
        secret_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    cont_sign = client.product_add("https://example.com/01028.jpg", "{\"code\":\"01028\"}")

    assert cont_sign == "sign-2"
    assert counters == {"token": 2, "add": 2}


def test_baidu_client_maps_operation_error_to_generic_code():
    """百度业务错误应映射成统一的通用错误码。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/2.0/token":
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/add":
            return httpx.Response(200, json={"error_code": 216201, "error_msg": "brief invalid"})
        raise AssertionError(f"未预期的请求路径: {request.url.path}")

    client = BaiduImageSearchClient(
        api_key="api-key",
        secret_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ImageIndexError) as exc_info:
        client.product_add("https://example.com/01028.jpg", "{\"code\":\"01028\"}")

    assert exc_info.value.code == "IMAGE_UPLOAD_FAILED"
    assert "[216201] brief invalid" in exc_info.value.message


def test_baidu_client_product_search_uses_url_and_returns_hits():
    """商品检索应携带图片地址和 rn，并把返回结果转成候选对象。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/2.0/token":
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/search":
            assert request.url.params["access_token"] == "token-1"
            form = parse_qs(request.content.decode("utf-8"))
            assert form["url"] == ["https://example.com/query.jpg"]
            assert form["rn"] == ["3"]
            return httpx.Response(200, json={
                "result": [
                    {
                        "score": 0.96,
                        "brief": '{"code":"01028","name":"宝可梦睡姿明盒"}',
                        "cont_sign": "sign-1",
                    }
                ]
            })
        raise AssertionError(f"未预期的请求路径: {request.url.path}")

    client = BaiduImageSearchClient(
        api_key="api-key",
        secret_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    hits = client.product_search("https://example.com/query.jpg", rn=3)

    assert hits == [
        BaiduProductSearchHit(
            score=0.96,
            brief='{"code":"01028","name":"宝可梦睡姿明盒"}',
            cont_sign="sign-1",
        )
    ]


def test_baidu_client_product_search_supports_data_url():
    """商品检索应支持 data URL 图片。"""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/2.0/token":
            return httpx.Response(200, json={"access_token": "token-1", "expires_in": 3600})
        if request.url.path == "/rest/2.0/image-classify/v1/realtime_search/product/search":
            form = parse_qs(request.content.decode("utf-8"))
            assert form["image"] == ["QUJDREVGRw=="]
            assert form["rn"] == ["3"]
            return httpx.Response(200, json={"result": []})
        raise AssertionError(f"未预期的请求路径: {request.url.path}")

    client = BaiduImageSearchClient(
        api_key="api-key",
        secret_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    assert client.product_search("data:image/png;base64,QUJDREVGRw==", rn=3) == []


def test_inventory_image_search_service_keeps_top3_hits_over_threshold():
    """图片检索服务应过滤低分结果，并只保留按分数排序的前三个商品。"""

    class FakeSearchClient:
        def __init__(self):
            self.calls = []

        def product_search(self, image_ref: str, rn: int):
            self.calls.append((image_ref, rn))
            if image_ref == "image-1":
                return [
                    BaiduProductSearchHit(0.95, '{"code":"01028","name":"宝可梦睡姿明盒"}'),
                    BaiduProductSearchHit(0.84, '{"code":"0102250","name":"宝可梦立牌"}'),
                    BaiduProductSearchHit(0.93, '{"code":"0100700","name":""}'),
                ]
            return [
                BaiduProductSearchHit(0.91, '{"code":"0102250","name":"宝可梦立牌"}'),
                BaiduProductSearchHit(0.90, '{"code":"0100701","name":"宝可梦挂件"}'),
                BaiduProductSearchHit(0.89, '{"code":"0100702","name":"宝可梦徽章"}'),
                BaiduProductSearchHit(0.88, '{"code":"0100703","name":"宝可梦卡套"}'),
            ]

    fake_client = FakeSearchClient()
    service = InventoryImageSearchService(client=fake_client)

    items = service.search_inventory_items(["image-1", "image-2"])

    assert fake_client.calls == [("image-1", 3), ("image-2", 3)]
    assert items == [
        {"item_code": "01028", "item_name": "宝可梦睡姿明盒"},
        {"item_code": "0102250", "item_name": "宝可梦立牌"},
        {"item_code": "0100701", "item_name": "宝可梦挂件"},
    ]
