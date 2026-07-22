---
id: fastapi_dependency_lifecycle
title: FastAPI 依赖项生命周期边界
domain: fastapi
source_type: theory
content_kind: mechanism
tags: [fastapi, python, 依赖注入]
aliases: [yield 依赖, lifespan 资源]
difficulty: intermediate
question_patterns:
  - FastAPI 中使用 yield 的依赖何时执行清理？
  - 请求级依赖和应用级 lifespan 应怎样划分？
references:
  - title: 使用 yield 的依赖项
    url: https://fastapi.tiangolo.com/zh/tutorial/dependencies/dependencies-with-yield/
    source_kind: official_cn
    publisher: FastAPI
  - title: FastAPI 生命周期事件
    url: https://fastapi.tiangolo.com/zh/advanced/events/
    source_kind: official_cn
    publisher: FastAPI
---
# FastAPI 依赖项生命周期边界

## 核心结论
请求级资源应由依赖项创建并在请求结束时释放，应用级共享资源应由 lifespan 在进程启动和关闭时管理。两者解决的是资源所有权与清理顺序，不能用于掩盖阻塞调用或错误的并发模型。

## 机制与边界
使用 yield 的依赖在 yield 之前准备资源，把值传给下游，在响应与异常处理阶段之后执行清理代码。相同请求内的依赖结果通常可复用，但不同请求之间不能依赖请求缓存共享状态。数据库会话适合请求级管理，连接池、模型客户端和长期后台资源适合 lifespan。每个工作进程都有自己的应用生命周期，因此共享资源数量会随进程数增长。

## 常见错误
在普通依赖里每次创建昂贵客户端会增加延迟；把请求会话存成全局变量会造成并发污染。清理代码吞掉业务异常会破坏错误语义，而在清理阶段执行长时间阻塞操作会延迟响应后的资源释放。

## 工程权衡
请求级资源隔离清晰，但创建成本较高；应用级资源复用效率高，却要求线程安全、连接恢复和关闭顺序正确。可通过连接池或轻量会话在两者之间平衡，同时为清理失败保留日志和指标。

## 可观察评分信号
优秀回答会画出进入依赖、处理函数、响应、异常和退出代码的顺序，说明资源由谁拥有、何时释放，并能区分依赖生命周期问题、事件循环阻塞和下游超时三类故障。
