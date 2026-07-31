---
id: postgresql_connection_saturation
title: PostgreSQL 连接耗尽与排队放大
domain: postgresql
source_type: theory
content_kind: failure_mode
tags: [postgresql, reliability, 连接耗尽]
aliases: [连接风暴, 连接池饱和]
difficulty: intermediate
question_patterns:
  - PostgreSQL 连接耗尽时为什么应用扩容可能让故障更严重？
  - 如何区分数据库连接上限和应用连接池排队？
references:
  - title: Azure Database for PostgreSQL 灵活服务器中的限制
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/configure-maintain/concepts-limits
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 监视 Azure Database for PostgreSQL
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/monitor/concepts-monitoring
    source_kind: official_cn
    publisher: Microsoft Learn
---
# PostgreSQL 连接耗尽与排队放大

## 核心结论
连接耗尽通常不是一个孤立的数据库错误，而是一条排队和重试放大链。请求先等待应用连接池，超时后触发重试；自动扩容又创建更多连接池，最终让数据库连接、CPU 和内存同时饱和。恢复时如果所有实例一起重连，还可能形成第二次连接风暴。

## 机制与边界
数据库达到连接上限后，新会话被拒绝；在此之前，大量活跃后端也可能因调度和内存压力显著降低吞吐。应用池等待时间会增加端到端延迟，超时重试则提高到达率。滚动发布中新旧实例重叠、健康检查频繁建连、后台任务与在线请求共用池，都会让稳定态估算失效。

## 常见错误
直接提高 max_connections 可能暂时减少拒绝，却把瓶颈转化为更严重的资源争用。无上限重试和同步重连会放大故障。只看数据库当前连接数而不看应用等待队列，会在连接尚未达到绝对上限时漏掉用户侧超时。故障中继续自动扩容也可能增加压力。

## 工程权衡
限制池大小和快速失败可以保护数据库，但需要业务降级和背压。连接代理能平滑短连接，却不能修复长事务、慢查询和不受控并发。恢复策略应加入抖动、分批放量和连接预热上限，并为管理连接保留独立预算。

## 可观察评分信号
回答应描述请求到连接池、数据库后端和重试的完整链路，给出全局连接预算、获取超时、重试上限和恢复节流，并观察等待队列、连接拒绝、活跃会话、数据库资源和端到端错误率。
