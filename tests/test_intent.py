"""意图识别接口测试，mock LLM 调用。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _mock_llm_response(content: str):
    """构造 mock 的 OpenAI 响应对象。"""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# ---- 查物流 ----

@patch("app.llm.OpenAI")
def test_query_logistics_with_order_no(mock_openai_cls):
    """客户查物流，消息中包含物流单号。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_logistics", "status": "success", "order_no": "SF1234567890"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [],
        "at_message": {"type": "text", "content": "@AI 帮我查一下物流 SF1234567890"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query_logistics"
    assert data["status"] == "success"
    assert data["order_no"] == "SF1234567890"


@patch("app.llm.OpenAI")
def test_query_logistics_no_order_no(mock_openai_cls):
    """客户查物流，但未提供物流单号。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_logistics", "status": "no_tracking_no"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [],
        "at_message": {"type": "text", "content": "@AI 我的快递到哪了"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query_logistics"
    assert data["status"] == "no_tracking_no"


# ---- 查库存（纯文本） ----

@patch("app.llm.OpenAI")
def test_query_inventory_text_success(mock_openai_cls):
    """客户查库存（纯文本），提取到商品信息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_inventory", "status": "success", "item_code": "", "item_name": "宝可梦睡姿明盒"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [],
        "at_message": {"type": "text", "content": "@AI 宝可梦睡姿明盒有货吗？"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query_inventory"
    assert data["status"] == "success"
    assert data["item_name"] == "宝可梦睡姿明盒"


@patch("app.llm.OpenAI")
def test_query_inventory_text_no_info(mock_openai_cls):
    """客户查库存（纯文本），未提取到商品信息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_inventory", "status": "no_info_extracted"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [],
        "at_message": {"type": "text", "content": "@AI 有货吗"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query_inventory"
    assert data["status"] == "no_info_extracted"


# ---- 查库存（含图片） ----

@patch("app.llm.OpenAI")
def test_query_inventory_with_image(mock_openai_cls):
    """客户查库存，前文消息包含图片，应使用 vision 模型。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_inventory", "status": "success", "item_code": "01028", "item_name": "宝可梦睡姿明盒"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [
            {"type": "image", "url": "https://img.pokemondb.net/artwork/large/pikachu.jpg"},
        ],
        "at_message": {"type": "text", "content": "@AI 有货吗"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    data = resp.json()
    assert data["intent"] == "query_inventory"
    assert data["item_code"] == "01028"

    # 验证使用了 vision 模型配置
    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = call_kwargs.kwargs["messages"][1]["content"]
    assert isinstance(user_content, list)  # vision 格式是 list
    assert any(p.get("type") == "image_url" for p in user_content)


# ---- 其他意图 ----

@patch("app.llm.OpenAI")
def test_not_sure_intent(mock_openai_cls):
    """无法识别的意图。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "not_sure_intent"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = client.post("/analyze_inventory_intent", json={
        "before_messages": [],
        "at_message": {"type": "text", "content": "@AI 你好"},
        "after_messages": [],
    })

    assert resp.status_code == 200
    assert resp.json()["intent"] == "not_sure_intent"


# ---- 辅助函数单元测试 ----

from app.llm import _has_image, _build_user_text, _build_user_content
from app.schemas import AnalyzeRequest, Message


def test_has_image_true():
    req = AnalyzeRequest(
        before_messages=[Message(type="image", url="https://img.pokemondb.net/artwork/large/pikachu.jpg")],
        at_message=Message(type="text", content="有货吗"),
    )
    assert _has_image(req) is True


def test_has_image_false():
    req = AnalyzeRequest(
        before_messages=[Message(type="text", content="前文")],
        at_message=Message(type="text", content="@AI 查物流"),
    )
    assert _has_image(req) is False


def test_build_user_text():
    req = AnalyzeRequest(
        before_messages=[Message(type="text", content="之前的消息")],
        at_message=Message(type="text", content="@AI 有货吗"),
        after_messages=[Message(type="text", content="之后的消息")],
    )
    text = _build_user_text(req)
    assert "[前文消息] 之前的消息" in text
    assert "[@AI消息] @AI 有货吗" in text
    assert "[后续消息] 之后的消息" in text


def test_build_user_content_with_image():
    req = AnalyzeRequest(
        before_messages=[Message(type="image", url="https://img.pokemondb.net/artwork/large/pikachu.jpg")],
        at_message=Message(type="text", content="@AI 有货吗"),
    )
    parts = _build_user_content(req)
    types = [p["type"] for p in parts]
    assert "image_url" in types
    assert "text" in types
