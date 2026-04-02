"""意图识别接口测试，mock LLM 调用。"""

from unittest.mock import MagicMock, patch

from app.llm import _build_user_content, _build_user_text, _has_image, _parse_llm_json
from app.main import analyze_inventory_intent
from app.schemas import AnalyzeRequest, Message


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
        '[{"intent": "query_logistics", "status": "success", "order_no": "SF1234567890"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 帮我查一下物流 SF1234567890"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_logistics"
    assert resp[0].status == "success"
    assert resp[0].order_no == "SF1234567890"
    assert resp[0].items is None


@patch("app.llm.OpenAI")
def test_query_logistics_no_order_no(mock_openai_cls):
    """客户查物流，但未提供物流单号。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_logistics", "status": "no_tracking_no"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 我的快递到哪了"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_logistics"
    assert resp[0].status == "no_tracking_no"
    assert resp[0].order_no is None
    assert resp[0].items is None


# ---- 查库存 ----

@patch("app.llm.OpenAI")
def test_query_inventory_text_success(mock_openai_cls):
    """客户查库存（纯文本），提取到商品信息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "success", "items": [{"item_name": "宝可梦睡姿明盒"}]}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 宝可梦睡姿明盒有货吗？"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].status == "success"
    assert resp[0].items is not None
    assert len(resp[0].items) == 1
    assert resp[0].items[0].item_name == "宝可梦睡姿明盒"
    assert resp[0].items[0].item_code is None


@patch("app.llm.OpenAI")
def test_query_inventory_text_success_legacy_shape(mock_openai_cls):
    """客户查库存（纯文本），兼容旧的单商品返回结构。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"intent": "query_inventory", "status": "success", "item_code": "", "item_name": "宝可梦睡姿明盒"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 宝可梦睡姿明盒有货吗？"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].status == "success"
    assert resp[0].items is not None
    assert len(resp[0].items) == 1
    assert resp[0].items[0].item_name == "宝可梦睡姿明盒"
    assert resp[0].items[0].item_code is None


@patch("app.llm.OpenAI")
def test_query_inventory_text_no_info(mock_openai_cls):
    """客户查库存（纯文本），未提取到商品信息。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "no_info_extracted"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 有货吗"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].status == "no_info_extracted"
    assert resp[0].order_no is None
    assert resp[0].items is None


@patch("app.llm.OpenAI")
def test_query_inventory_with_image(mock_openai_cls):
    """客户查库存，前文消息包含图片，应使用 vision 模型。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "success", "items": [{"item_code": "01028", "item_name": "宝可梦睡姿明盒"}]}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[
            Message(type="image", url="https://img.pokemondb.net/artwork/large/pikachu.jpg"),
        ],
        at_message=Message(type="text", content="@AI 有货吗"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].items is not None
    assert len(resp[0].items) == 1
    assert resp[0].items[0].item_code == "01028"
    assert resp[0].items[0].item_name == "宝可梦睡姿明盒"

    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = call_kwargs.kwargs["messages"][1]["content"]
    assert isinstance(user_content, list)
    assert any(p.get("type") == "image_url" for p in user_content)


@patch("app.llm.OpenAI")
def test_query_inventory_with_multiple_items(mock_openai_cls):
    """客户查库存，图片中包含多个商品，应聚合在一个 inventory 结果里。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "success", "items": [{"item_code": "0102250"}, {"item_code": "0100700"}, {"item_code": "0102250"}]}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[
            Message(type="image", url="https://example.com/multi-items.png"),
        ],
        at_message=Message(type="text", content="@AI 这两个有货吗"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].status == "success"
    assert resp[0].items is not None
    assert len(resp[0].items) == 2
    assert {item.item_code for item in resp[0].items} == {"0102250", "0100700"}


@patch("app.llm.OpenAI")
def test_query_inventory_with_image_json_fence(mock_openai_cls):
    """客户查库存，兼容 LLM 返回 Markdown 代码块包裹的 JSON 数组。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '```json\n[{"intent": "query_inventory", "status": "success", "items": [{"item_code": "0100700"}]}]\n```'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[
            Message(type="image", url="https://example.com/fenced-json.png"),
        ],
        at_message=Message(type="text", content="@AI 这个有货吗"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "query_inventory"
    assert resp[0].status == "success"
    assert resp[0].items is not None
    assert len(resp[0].items) == 1
    assert resp[0].items[0].item_code == "0100700"


# ---- 获取报价单 ----

@patch("app.llm.OpenAI")
def test_get_quote_success(mock_openai_cls):
    """客户明确要报价单，应识别为 get_quote。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "get_quote", "status": "success"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 发一下报价单"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "get_quote"
    assert resp[0].status == "success"
    assert resp[0].order_no is None
    assert resp[0].items is None


# ---- 多意图 ----

@patch("app.llm.OpenAI")
def test_query_inventory_and_get_quote(mock_openai_cls):
    """同时提到库存和报价单时，应返回两个意图结果。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "success", "items": [{"item_code": "01028"}]}, {"intent": "get_quote", "status": "success"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 这个有货吗，顺便发下报价单"),
        after_messages=[],
    ))

    assert [item.intent for item in resp] == ["query_inventory", "get_quote"]
    assert resp[0].items is not None
    assert resp[0].items[0].item_code == "01028"
    assert resp[1].status == "success"


@patch("app.llm.OpenAI")
def test_query_logistics_and_get_quote_order(mock_openai_cls):
    """多意图应按固定顺序输出。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "get_quote", "status": "success"}, {"intent": "query_logistics", "status": "success", "order_no": "SF1234567890"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 帮我查一下物流 SF1234567890，再发一下报价单"),
        after_messages=[],
    ))

    assert [item.intent for item in resp] == ["query_logistics", "get_quote"]
    assert resp[0].order_no == "SF1234567890"
    assert resp[1].status == "success"


@patch("app.llm.OpenAI")
def test_multi_intent_merges_duplicate_inventory(mock_openai_cls):
    """多个 inventory 结果应合并为一个，并去重商品。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "query_inventory", "status": "success", "items": [{"item_code": "0102250"}]}, {"intent": "query_inventory", "status": "success", "items": [{"item_code": "0100700"}, {"item_code": "0102250"}]}, {"intent": "get_quote", "status": "success"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 这两个有货吗，再发下报价单"),
        after_messages=[],
    ))

    assert [item.intent for item in resp] == ["query_inventory", "get_quote"]
    assert resp[0].items is not None
    assert len(resp[0].items) == 2
    assert {item.item_code for item in resp[0].items} == {"0102250", "0100700"}


@patch("app.llm.OpenAI")
def test_not_sure_intent_only_when_no_concrete_intent(mock_openai_cls):
    """识别到明确意图时，不应返回 not_sure_intent。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "not_sure_intent"}, {"intent": "get_quote", "status": "success"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 发一下报价单"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "get_quote"
    assert resp[0].status == "success"


# ---- 其他意图 ----

@patch("app.llm.OpenAI")
def test_not_sure_intent(mock_openai_cls):
    """无法识别的意图。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '[{"intent": "not_sure_intent"}]'
    )
    mock_openai_cls.return_value = mock_client

    resp = analyze_inventory_intent(AnalyzeRequest(
        before_messages=[],
        at_message=Message(type="text", content="@AI 你好"),
        after_messages=[],
    ))

    assert len(resp) == 1
    assert resp[0].intent == "not_sure_intent"
    assert resp[0].status is None
    assert resp[0].order_no is None
    assert resp[0].items is None


# ---- 辅助函数单元测试 ----

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


def test_parse_llm_json_with_markdown_fence():
    data = _parse_llm_json('```json\n[{"intent": "not_sure_intent"}]\n```')
    assert data == [{"intent": "not_sure_intent"}]
