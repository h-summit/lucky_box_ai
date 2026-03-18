"""回复语生成接口测试，mock LLM 调用。"""

from unittest.mock import MagicMock, patch

from app.main import customer_relationship_management, greetings, holiday_greetings
from app.schemas import (
    CustomerRelationshipManagementRequest,
    GreetingsRequest,
    HistoryMessage,
    HolidayGreetingsRequest,
)


def _mock_llm_response(content: str):
    """构造 mock 的 OpenAI 响应对象。"""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


@patch("app.llm.OpenAI")
def test_greetings(mock_openai_cls):
    """打招呼接口返回生成的回复语。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"response": "您好呀，我们这边主营宝可梦周边，最近有不少现货新品，您可以告诉我想看哪一类。"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = greetings(GreetingsRequest(
        prompt="给新客户打一段招呼语",
        product_info="主营宝可梦周边、盲盒、手办，支持批发和零售",
    ))

    assert resp.response == "您好呀，我们这边主营宝可梦周边，最近有不少现货新品，您可以告诉我想看哪一类。"

    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = call_kwargs.kwargs["messages"][1]["content"]
    assert "给新客户打一段招呼语" in user_content
    assert "主营宝可梦周边、盲盒、手办，支持批发和零售" in user_content


@patch("app.llm.OpenAI")
def test_holiday_greetings(mock_openai_cls):
    """节日问候接口返回生成的回复语。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"response": "中秋快乐，感谢您一直以来的支持，祝您阖家团圆，最近想看的款式也可以随时发我。"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = holiday_greetings(HolidayGreetingsRequest(
        holiday="中秋节",
        time_now="2026-09-17 10:00:00",
        history=[
            HistoryMessage(role="user", content="上次那批宝可梦睡姿还有吗"),
            HistoryMessage(role="assistant", content="部分款还有现货，您要的话我可以给您整理"),
        ],
    ))

    assert resp.response == "中秋快乐，感谢您一直以来的支持，祝您阖家团圆，最近想看的款式也可以随时发我。"

    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = call_kwargs.kwargs["messages"][1]["content"]
    assert "中秋节" in user_content
    assert "2026-09-17 10:00:00" in user_content
    assert "上次那批宝可梦睡姿还有吗" in user_content


@patch("app.llm.OpenAI")
def test_customer_relationship_management(mock_openai_cls):
    """客情维护接口返回生成的回复语。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_llm_response(
        '{"response": "这段时间一直没打扰您，最近我们这边到了一批新款宝可梦周边，您如果想看新品我可以给您发一版清单。"}'
    )
    mock_openai_cls.return_value = mock_client

    resp = customer_relationship_management(CustomerRelationshipManagementRequest(
        time_delay="距离上次联系已过去30天",
        time_now="2026-03-18 15:30:00",
        history=[
            HistoryMessage(role="user", content="上次发我的新品图我看到了"),
            HistoryMessage(role="assistant", content="好的，您有想重点了解的系列可以随时告诉我"),
        ],
    ))

    assert resp.response == "这段时间一直没打扰您，最近我们这边到了一批新款宝可梦周边，您如果想看新品我可以给您发一版清单。"

    call_kwargs = mock_client.chat.completions.create.call_args
    user_content = call_kwargs.kwargs["messages"][1]["content"]
    assert "距离上次联系已过去30天" in user_content
    assert "2026-03-18 15:30:00" in user_content
    assert "上次发我的新品图我看到了" in user_content
