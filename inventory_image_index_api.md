# 图片检索库异步入库接口设计

## 1. 目标

为“查库存”能力补充图片检索库的数据入库接口。

本次只设计两类接口：

- 异步提交一批商品图片入库任务
- 查询该任务当前进度和失败明细

本次接口只处理图片入库，不处理查库存主流程。

## 2. 已确认约束

- 单次请求传入一批商品，每个商品包含 `code`、`name` 和三张候选图片：
  - `picture_url`
  - `small_package_picture_url`
  - `middle_package_picture_url`
- 三张图片不一定都有值。
- 只处理本次传入的非空图片。
- 空值表示忽略，不表示删除旧图。
- 旧图保留。
- 入库语义按 `(code, image_type)` 做 upsert。
- 必须异步执行。
- 需要提供任务查询接口，用于查看：
  - 当前已成功入库多少张图片
  - 哪些图片没有入库成功

## 3. 设计原则

- 统计口径按“图片”维度，不按“商品”维度。
- 一个商品最多拆成 3 条图片入库记录。
- 任务查询接口默认返回汇总信息和失败明细，不返回全部成功明细。
- 同一批次内如果出现相同的 `(code, image_type)`，以最后一条非空图片 URL 为准。

## 4. 图片类型映射

请求里的三个图片字段映射为固定的 `image_type`：

| 请求字段 | image_type | 含义 |
|---|---|---|
| `picture_url` | `product` | 商品主图 |
| `small_package_picture_url` | `small_package` | 小包图 |
| `middle_package_picture_url` | `middle_package` | 中包图 |

## 5. 接口一：提交异步入库任务

### 5.1 路径

`POST /inventory_image_index/tasks`

### 5.2 请求体

```json
{
  "products": [
    {
      "code": "01028",
      "name": "宝可梦睡姿明盒",
      "picture_url": "https://example.com/images/01028-product.jpg",
      "small_package_picture_url": "https://example.com/images/01028-small.jpg",
      "middle_package_picture_url": null
    },
    {
      "code": "0102250",
      "name": "宝可梦立牌",
      "picture_url": "",
      "small_package_picture_url": "https://example.com/images/0102250-small.jpg",
      "middle_package_picture_url": "https://example.com/images/0102250-middle.jpg"
    }
  ]
}
```

### 5.3 字段说明

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `products` | array | 是 | 商品数组 |
| `products[].code` | string | 是 | 商品编码，也是图片记录的业务主键之一 |
| `products[].name` | string | 是 | 商品名称，入库时作为元数据保存 |
| `products[].picture_url` | string \| null | 否 | 商品主图 URL，空值忽略 |
| `products[].small_package_picture_url` | string \| null | 否 | 小包图 URL，空值忽略 |
| `products[].middle_package_picture_url` | string \| null | 否 | 中包图 URL，空值忽略 |

### 5.4 服务端处理规则

1. 遍历 `products`。
2. 将每个商品的 3 个图片字段展开为最多 3 条图片入库记录。
3. 仅保留非空 URL 的图片记录。
4. 每条图片记录的唯一键为 `(code, image_type)`。
5. 如果同一个任务里出现重复的 `(code, image_type)`，只保留最后一条非空记录。
6. 创建异步任务后立即返回，不等待图片搜索入库完成。

### 5.5 响应体

HTTP 状态码建议使用 `202 Accepted`。

```json
{
  "task_id": "imgidx_20260412_000001",
  "status": "pending",
  "total_product_count": 2,
  "submitted_image_count": 4,
  "ignored_empty_image_count": 2,
  "created_at": "2026-04-12T18:30:00+08:00"
}
```

### 5.6 响应字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 异步任务 ID |
| `status` | string | 初始状态，固定返回 `pending` |
| `total_product_count` | integer | 请求中的商品数 |
| `submitted_image_count` | integer | 实际进入异步任务的图片数，只统计非空图片 |
| `ignored_empty_image_count` | integer | 因为空值而被忽略的图片槽位数 |
| `created_at` | string | 任务创建时间，ISO 8601 格式 |

### 5.7 失败响应

当请求体不合法时，返回 `400 Bad Request`：

```json
{
  "error": "products 不能为空"
}
```

## 6. 接口二：查询任务进度和结果

### 6.1 路径

`GET /inventory_image_index/tasks/{task_id}`

### 6.2 响应体

```json
{
  "task_id": "imgidx_20260412_000001",
  "status": "running",
  "total_product_count": 2,
  "submitted_image_count": 4,
  "processed_image_count": 3,
  "succeeded_image_count": 2,
  "failed_image_count": 1,
  "pending_image_count": 1,
  "ignored_empty_image_count": 2,
  "created_at": "2026-04-12T18:30:00+08:00",
  "started_at": "2026-04-12T18:30:02+08:00",
  "finished_at": null,
  "failed_items": [
    {
      "code": "0102250",
      "name": "宝可梦立牌",
      "image_type": "middle_package",
      "image_url": "https://example.com/images/0102250-middle.jpg",
      "error_code": "IMAGE_UPLOAD_FAILED",
      "error_message": "百度图片入库失败"
    }
  ]
}
```

### 6.3 响应字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | string | 异步任务 ID |
| `status` | string | 任务状态，见“任务状态定义” |
| `total_product_count` | integer | 请求中的商品数 |
| `submitted_image_count` | integer | 实际进入异步任务的图片数 |
| `processed_image_count` | integer | 已处理完成的图片数，等于成功数加失败数 |
| `succeeded_image_count` | integer | 已成功入库的图片数 |
| `failed_image_count` | integer | 入库失败的图片数 |
| `pending_image_count` | integer | 尚未处理完成的图片数 |
| `ignored_empty_image_count` | integer | 因为空值而被忽略的图片槽位数 |
| `created_at` | string | 任务创建时间 |
| `started_at` | string \| null | 任务开始执行时间 |
| `finished_at` | string \| null | 任务结束时间，未结束时为 `null` |
| `failed_items` | array | 失败图片明细，仅返回失败项 |

### 6.4 `failed_items` 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `code` | string | 商品编码 |
| `name` | string | 商品名称 |
| `image_type` | string | 图片类型：`product` / `small_package` / `middle_package` |
| `image_url` | string | 本次尝试入库的图片 URL |
| `error_code` | string | 失败码 |
| `error_message` | string | 失败原因 |

### 6.5 完成态示例

全部成功：

```json
{
  "task_id": "imgidx_20260412_000001",
  "status": "success",
  "total_product_count": 2,
  "submitted_image_count": 4,
  "processed_image_count": 4,
  "succeeded_image_count": 4,
  "failed_image_count": 0,
  "pending_image_count": 0,
  "ignored_empty_image_count": 2,
  "created_at": "2026-04-12T18:30:00+08:00",
  "started_at": "2026-04-12T18:30:02+08:00",
  "finished_at": "2026-04-12T18:30:10+08:00",
  "failed_items": []
}
```

部分成功：

```json
{
  "task_id": "imgidx_20260412_000001",
  "status": "partial_success",
  "total_product_count": 2,
  "submitted_image_count": 4,
  "processed_image_count": 4,
  "succeeded_image_count": 3,
  "failed_image_count": 1,
  "pending_image_count": 0,
  "ignored_empty_image_count": 2,
  "created_at": "2026-04-12T18:30:00+08:00",
  "started_at": "2026-04-12T18:30:02+08:00",
  "finished_at": "2026-04-12T18:30:10+08:00",
  "failed_items": [
    {
      "code": "0102250",
      "name": "宝可梦立牌",
      "image_type": "middle_package",
      "image_url": "https://example.com/images/0102250-middle.jpg",
      "error_code": "IMAGE_DOWNLOAD_FAILED",
      "error_message": "图片下载失败"
    }
  ]
}
```

全部失败：

```json
{
  "task_id": "imgidx_20260412_000001",
  "status": "failed",
  "total_product_count": 2,
  "submitted_image_count": 4,
  "processed_image_count": 4,
  "succeeded_image_count": 0,
  "failed_image_count": 4,
  "pending_image_count": 0,
  "ignored_empty_image_count": 2,
  "created_at": "2026-04-12T18:30:00+08:00",
  "started_at": "2026-04-12T18:30:02+08:00",
  "finished_at": "2026-04-12T18:30:10+08:00",
  "failed_items": [
    {
      "code": "01028",
      "name": "宝可梦睡姿明盒",
      "image_type": "product",
      "image_url": "https://example.com/images/01028-product.jpg",
      "error_code": "IMAGE_UPLOAD_FAILED",
      "error_message": "百度图片入库失败"
    }
  ]
}
```

### 6.6 任务不存在

返回 `404 Not Found`：

```json
{
  "error": "task_id 不存在"
}
```

## 7. 任务状态定义

| 状态 | 含义 |
|---|---|
| `pending` | 任务已创建，尚未开始执行 |
| `running` | 任务执行中 |
| `success` | 全部图片都入库成功 |
| `partial_success` | 部分图片成功，部分图片失败 |
| `failed` | 所有图片都入库失败 |

## 8. 服务端内部数据展开示例

原始请求：

```json
{
  "products": [
    {
      "code": "01028",
      "name": "宝可梦睡姿明盒",
      "picture_url": "https://example.com/images/01028-product.jpg",
      "small_package_picture_url": "https://example.com/images/01028-small.jpg",
      "middle_package_picture_url": null
    }
  ]
}
```

服务端展开后应得到两条待处理图片记录：

```json
[
  {
    "code": "01028",
    "name": "宝可梦睡姿明盒",
    "image_type": "product",
    "image_url": "https://example.com/images/01028-product.jpg"
  },
  {
    "code": "01028",
    "name": "宝可梦睡姿明盒",
    "image_type": "small_package",
    "image_url": "https://example.com/images/01028-small.jpg"
  }
]
```

## 9. 与百度图片搜索的映射建议

这一层是服务端内部实现建议，不暴露给调用方。

- 建议把 `code`、`name`、`image_type` 写入 `brief`
- 本地用 SQLite 保存 `(code, image_type) -> cont_sign`
- 更新同一业务图片时，优先复用本地映射的 `cont_sign`

```json
{
  "code": "01028",
  "name": "宝可梦睡姿明盒",
  "image_type": "product"
}
```

这样查询命中后，可以直接从返回结果中还原出 `code` 和 `name`。

## 10. 本版不包含的能力

以下内容本版先不设计：

- 删除图片接口
- 批量取消任务
- 任务列表查询
- 成功明细分页
- 回调通知
- 重试接口

如果后续需要，再单独补充。
