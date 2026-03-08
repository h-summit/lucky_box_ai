# Lucky Box AI

微信群聊客服意图识别服务。通过 LLM 识别客户意图（查物流/查库存），提取关键信息供后端调用。

## 配置

通过环境变量配置，或创建 `.env` 文件（参考 `.env.example`）：

| 变量 | 说明 |
|------|------|
| `TEXT_LLM_BASE_URL` | 纯文本模型 API 地址 |
| `TEXT_LLM_API_KEY` | 纯文本模型 Key |
| `TEXT_LLM_MODEL` | 纯文本模型名称 |
| `VISION_LLM_BASE_URL` | 图片问答模型 API 地址 |
| `VISION_LLM_API_KEY` | 图片问答模型 Key |
| `VISION_LLM_MODEL` | 图片问答模型名称 |

## 开发部署

```bash
# 创建虚拟环境
python -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入实际的 API Key 等配置

# 启动开发服务器（热重载）
uvicorn app.main:app --reload
```

访问 http://localhost:8000/docs 查看接口文档。

## Docker 部署

```bash
# 构建镜像
docker build -t lucky-box-ai .

# 运行（通过环境变量传入配置）
docker run -d -p 8000:8000 \
  -e TEXT_LLM_BASE_URL=https://api.openai.com/v1 \
  -e TEXT_LLM_API_KEY=sk-xxx \
  -e TEXT_LLM_MODEL=gpt-4o \
  -e VISION_LLM_BASE_URL=https://api.openai.com/v1 \
  -e VISION_LLM_API_KEY=sk-xxx \
  -e VISION_LLM_MODEL=gpt-4o \
  lucky-box-ai

# 或使用 .env 文件
docker run -d -p 8000:8000 --env-file .env lucky-box-ai
```

## 测试

### 单元测试

```bash
pip install -r requirements.txt
pytest
```

### curl 集成测试

确保服务已启动（本地或 Docker），然后运行以下命令：

**1. 查物流 - 包含物流单号**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 帮我查一下物流 SF1234567890"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_logistics"`, `status: "success"`, `order_no` 包含单号。

**2. 查物流 - 未提供单号**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 我的快递到哪了"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_logistics"`, `status: "no_tracking_no"`。

**3. 查库存 - 纯文本，提取到商品名称**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 宝可梦睡姿明盒有货吗？"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_inventory"`, `status: "success"`, `item_name: "宝可梦睡姿明盒"`。

**4. 查库存 - 纯文本，提取到商品编码**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 01028有现货吗？"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_inventory"`, `status: "success"`, `item_code: "01028"`。

**5. 查库存 - 纯文本，未提取到商品信息**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 有货吗"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_inventory"`, `status: "no_info_extracted"`。

**6. 查库存 - 前文含图片（使用 vision 模型）**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [
      {"type": "image", "url": "https://img.pokemondb.net/artwork/large/pikachu.jpg"}
    ],
    "at_message": {"type": "text", "content": "@AI 有货吗"},
    "after_messages": []
  }'
```

期望返回：`intent: "query_inventory"`, `status: "success"`, 包含从图片中提取的商品信息。

**7. 其他意图**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [],
    "at_message": {"type": "text", "content": "@AI 你好"},
    "after_messages": []
  }'
```

期望返回：`intent: "not_sure_intent"`。

**8. 完整上下文（前文 + @消息 + 后续消息）**

```bash
curl -X POST http://localhost:8000/analyze_inventory_intent \
  -H "Content-Type: application/json" \
  -d '{
    "before_messages": [
      {"type": "text", "content": "我之前买的那个订单"},
      {"type": "text", "content": "单号是 YT9876543210"}
    ],
    "at_message": {"type": "text", "content": "@AI 帮我查下物流"},
    "after_messages": [
      {"type": "text", "content": "谢谢"}
    ]
  }'
```

期望返回：`intent: "query_logistics"`, `status: "success"`, `order_no: "YT9876543210"`。