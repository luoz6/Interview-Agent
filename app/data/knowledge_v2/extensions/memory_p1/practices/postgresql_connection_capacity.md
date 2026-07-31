---
id: postgresql_connection_capacity
title: PostgreSQL 连接容量与池化预算
domain: postgresql
source_type: engineering_guide
content_kind: engineering_practice
tags: [postgresql, reliability, 连接池]
aliases: [连接预算, 连接池容量]
difficulty: intermediate
question_patterns:
  - PostgreSQL 连接池大小应该如何根据实例限制和并发计算？
  - 为什么增加应用实例可能让数据库连接先耗尽？
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
# PostgreSQL 连接容量与池化预算

## 核心结论
数据库连接不是可以随应用实例无限增长的免费资源。连接池设计应先取得实例允许的连接上限，扣除管理员、迁移、监控、复制和故障处理所需的保留量，再把剩余预算分配给所有应用进程。每个进程的池大小乘以进程数、实例数和发布期间的新旧副本数，才是数据库实际承受的最坏连接数量。

## 机制与边界
每条 PostgreSQL 会话会消耗后端进程、内存和调度资源。池过小会让请求在应用侧排队，池过大则把排队转移到数据库并放大上下文切换。连接代理可以复用短事务连接，但事务级代理对会话状态、临时表、预编译语句和长事务存在兼容边界。扩容、滚动发布和故障切换期间应按峰值副本数计算，而不是只看稳定态实例数。

## 常见错误
把 max_connections 全部分给业务会让维护和故障诊断失去入口。只配置单进程池大小而忽略多 worker、多 Pod 和灰度副本，会在发布时突然耗尽连接。连接获取超时设置过长会让请求堆积，过短则在瞬时抖动中制造大量失败。健康检查也不应为每次探测创建新连接。

## 工程权衡
较小连接池有利于保护数据库，但需要明确的应用排队、超时和背压。较大连接池可能降低低负载下的等待，却会在突发流量时加速饱和。应把连接获取耗时、活跃连接、空闲连接、等待队列、事务时长、数据库 CPU 与内存一起观察，并通过压测验证预算。

## 可观察评分信号
高质量回答应给出全局连接预算公式，说明保留连接、滚动发布倍数、获取超时和池化模式，并用活跃连接、等待数、获取延迟、事务时长和错误率验证方案。
