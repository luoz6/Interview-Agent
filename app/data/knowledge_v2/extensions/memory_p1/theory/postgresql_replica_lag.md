---
id: postgresql_replica_lag
title: PostgreSQL 只读副本延迟与陈旧读取
domain: postgresql
source_type: theory
content_kind: failure_mode
tags: [postgresql, reliability, 复制延迟]
aliases: [副本延迟, 陈旧读]
difficulty: intermediate
question_patterns:
  - PostgreSQL 只读副本延迟会怎样破坏读后写一致性？
  - 如何在副本积压时选择降级、回主库或拒绝读取？
references:
  - title: Azure Database for PostgreSQL 灵活服务器中的只读副本
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/read-replica/concepts-read-replicas
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: 监视 Azure Database for PostgreSQL
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/monitor/concepts-monitoring
    source_kind: official_cn
    publisher: Microsoft Learn
---
# PostgreSQL 只读副本延迟与陈旧读取

## 核心结论
只读副本提升读扩展和故障隔离能力，但它提供的是异步复制边界，不自动保证读后写一致性。写入成功后立即从副本读取，可能看不到新数据；当主库写入突增、网络受限、长查询或副本资源不足时，复制延迟会继续累积。

## 机制与边界
主库生成的日志需要传输并在副本回放。延迟既包括传输，也包括回放和资源排队。只观察副本是否在线不能证明数据新鲜。对账户状态、权限、库存和任务状态等强时效读取，应明确路由到主库、携带一致性令牌，或在延迟超过阈值时拒绝使用副本。

## 常见错误
把所有 GET 请求无条件路由到副本会产生难以复现的陈旧读。只按时间延迟判断可能忽略日志量和业务影响。副本积压时继续增加读流量会进一步拖慢回放。把副本提升为主库前如果没有确认数据差距，也可能丢失最近写入。

## 工程权衡
回主库读取能恢复新鲜度，但增加主库压力；等待副本追平会增加用户延迟；返回陈旧数据需要产品能够明确容忍。应按数据类型定义一致性等级，而不是全局选择一种策略。副本扩容也要独立规划 CPU、内存和存储吞吐。

## 可观察评分信号
回答应说明复制异步边界、业务可接受的新鲜度、延迟阈值、回主库或拒绝策略，并观察日志积压、回放延迟、主副资源、陈旧读错误和提升前的数据差距。
