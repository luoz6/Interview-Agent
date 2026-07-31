---
id: postgresql_monitoring_baseline
title: PostgreSQL 运行监控与证据基线
domain: postgresql
source_type: engineering_guide
content_kind: mechanism
tags: [postgresql, reliability, 可观测性]
aliases: [数据库监控, PostgreSQL 指标]
difficulty: beginner
question_patterns:
  - PostgreSQL 生产监控应该同时观察哪些负载和等待指标？
  - 如何用数据库指标区分慢查询、连接饱和和存储瓶颈？
references:
  - title: 监视 Azure Database for PostgreSQL
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/monitor/concepts-monitoring
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: Azure Database for PostgreSQL 灵活服务器中的限制
    url: https://learn.microsoft.com/zh-cn/azure/postgresql/configure-maintain/concepts-limits
    source_kind: official_cn
    publisher: Microsoft Learn
---
# PostgreSQL 运行监控与证据基线

## 核心结论
数据库健康不能由单一 CPU 指标判断。可靠的 PostgreSQL 监控需要同时覆盖连接与会话、查询延迟、事务、锁等待、缓存与存储、复制、错误和资源限制，并把数据库指标与应用请求、发布事件和容量变化放在同一时间线上分析。

## 机制与边界
CPU 高可能来自高效地处理更多请求，也可能来自缺失索引、执行计划变化或连接过多；存储延迟高可能导致查询等待，却不一定表现为持续高 CPU。活跃连接接近上限时，新请求会在应用池或数据库入口失败。长事务会延迟清理并扩大表膨胀，锁等待会让少量慢事务阻塞大量正常请求。只读副本还需要独立观察复制延迟和回放状态。

## 常见错误
只看平均延迟会隐藏尾部恶化，只看数据库指标会忽略应用重试形成的负载放大。告警阈值如果没有基线和持续时间，容易在短峰值中误报；阈值过宽又会错过逐步退化。把所有慢请求归因于 SQL，也会忽略连接获取、网络、磁盘和锁等待。

## 工程权衡
细粒度查询统计能提高定位能力，但会增加采集和存储成本。高频指标适合短期故障诊断，长期趋势应使用聚合桶。生产监控应为容量、可用性和性能分别设置服务目标，并保留发布、迁移、故障切换等变更标记。

## 可观察评分信号
回答应列出连接使用率、获取等待、查询 p95/p99、事务时长、锁等待、缓存命中、读写 IOPS、存储延迟、复制延迟和错误率，并说明如何按时间关联应用流量和发布事件。
