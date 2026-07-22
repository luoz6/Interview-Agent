---
id: fastapi_request_lifecycle
title: FastAPI 请求生命周期
domain: fastapi
source_type: theory
content_kind: hard_negative
tags: [fastapi, python, 请求生命周期]
aliases: [ASGI 请求链路, 请求处理顺序]
difficulty: beginner
question_patterns:
  - FastAPI 请求从进入 ASGI 到响应返回经历哪些阶段？
  - 依赖解析、参数校验和中间件的执行顺序是什么？
references:
  - title: FastAPI 中间件
    url: https://fastapi.tiangolo.com/zh/tutorial/middleware/
    source_kind: official_cn
    publisher: FastAPI
  - title: FastAPI 请求体
    url: https://fastapi.tiangolo.com/zh/tutorial/body/
    source_kind: official_cn
    publisher: FastAPI
---
# FastAPI 请求生命周期

## 核心结论
一个请求会依次穿过 ASGI 服务器和中间件，完成路由匹配、依赖解析、参数校验、处理函数执行与响应序列化，最后执行资源清理并返回客户端。理解顺序是定位延迟、异常和资源泄漏的基础。

## 机制与边界
外层中间件先接收请求，调用下游后再反向处理响应。路由匹配确定端点后，框架读取路径、查询、头和请求体，按模型执行转换与校验；失败时直接生成校验错误，不进入业务处理函数。依赖树按关系解析并可在同一请求内复用。处理函数返回值经过响应模型和编码，使用 yield 的依赖随后完成退出逻辑。

## 常见错误
把校验错误当作业务异常会造成错误码混乱。中间件读取请求体后若不正确传递，可能让下游无法再次读取。依赖中执行隐藏的网络调用会让路由耗时难以归因，而响应已经开始后再抛异常也无法可靠修改状态码。

## 工程权衡
中间件适合统一追踪、安全头和通用策略，业务授权与资源加载通常更适合依赖项。响应模型能提供稳定契约，但复杂转换会增加耗时。应把跨请求通用逻辑放在外层，把需要路由上下文的逻辑放在依赖或服务层。

## 可观察评分信号
优秀回答能按顺序说明各阶段，指出异常在哪一层转为响应，并通过 trace span 区分中间件、校验、依赖、业务和序列化耗时，同时说明清理失败如何记录而不泄露敏感数据。
