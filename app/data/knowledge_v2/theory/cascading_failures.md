---
id: cascading_failures
title: 服务级联故障与放大链路
domain: system-design
source_type: theory
content_kind: failure_mode
tags: [system-design, reliability, 级联故障]
aliases: [重试风暴, 故障放大]
difficulty: intermediate
question_patterns:
  - 下游变慢如何形成重试风暴和级联故障？
  - 熔断、隔离和超时应怎样配合？
references:
  - title: 重试模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/retry
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 断路器模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/circuit-breaker
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 舱壁模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/bulkhead
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 服务级联故障与放大链路

## 核心结论
级联故障通常从下游变慢开始，上游请求占住线程、连接和队列，超时后重试又增加负载，最终多个本来健康的组件一起饱和。治理重点是限制等待和放大，而不是让每个请求无限坚持成功。

## 机制与边界
入口设置总超时预算，下游调用使用更短超时并只对瞬时错误有限重试，配合指数退避和随机抖动。断路器在持续失败时快速拒绝，半开状态用少量探测判断恢复。舱壁为不同租户或依赖划分连接、线程和并发配额，避免一个故障耗尽全部资源。队列和限流控制进入系统的工作量，降级返回可接受的简化结果。

## 常见错误
每一层独立重试会成倍放大调用。超时设置过长会耗尽资源，过短则制造假失败。熔断器没有最小样本和恢复探测会频繁抖动；所有流量共享一个资源池会让非关键功能拖垮核心链路。

## 工程权衡
快速失败保护系统，但会增加用户可见失败；缓存与降级提高可用性，却可能返回陈旧或不完整结果。隔离减少故障范围，但降低资源共享效率。策略应按业务优先级和可恢复性分层。

## 可观察评分信号
回答应画出放大链路，计算多层重试倍数，并监控超时率、重试量、在途请求、连接池、队列年龄、熔断状态和降级比例，同时说明故障注入与恢复验证。
