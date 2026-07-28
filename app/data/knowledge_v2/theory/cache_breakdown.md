---
id: cache_breakdown
title: 热点缓存失效与缓存击穿
domain: redis
source_type: theory
content_kind: failure_mode
tags: [redis, 缓存, 缓存击穿]
aliases: [热点键失效, 并发回源]
difficulty: intermediate
question_patterns:
  - 热点缓存失效后怎样避免请求同时访问数据库？
  - 互斥重建和逻辑过期分别适合什么场景？
references:
  - title: 用于 Redis 的开发最佳做法
    url: https://learn.microsoft.com/zh-cn/azure/azure-cache-for-redis/cache-best-practices-development
    source_kind: official_cn
    publisher: Microsoft Learn
  - title: Cache-Aside 模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/cache-aside
    source_kind: official_cn
    publisher: Microsoft Learn
---
# 热点缓存失效与缓存击穿

## 核心结论
缓存击穿发生在高热度键失效或被淘汰后，大量并发请求同时回源，瞬间把压力转移给数据库。治理目标不是保证永不失效，而是限制同一数据的并发重建，并在后端接近上限时保护数据库。

## 机制与边界
Cache-Aside 在未命中时读取数据库并回填缓存。热点键可使用 single-flight 或短租约互斥，只让一个请求重建，其余请求等待、返回旧值或快速降级。逻辑过期保留旧值，由后台刷新减少用户等待，但允许短时间陈旧。过期时间加入随机抖动可降低大量键同时失效。所有方案还需要数据库并发上限、超时和热点识别。

## 常见错误
只延长过期时间会把风险推迟，并增加陈旧数据时间。锁没有有效期可能永久阻塞，等待方无限排队会形成新的延迟峰值。把缓存穿透、击穿和雪崩混为一谈，会选错保护策略。

## 工程权衡
互斥重建一致性较直观，但锁持有者变慢会放大等待；逻辑过期可用性高，却需要接受旧值。主动预热适合可预测热点，但不能覆盖突发热点。应根据数据新鲜度、回源成本和数据库余量组合使用。

## 可观察评分信号
回答应给出单键请求量、未命中率、回源并发、重建时长、等待队列和数据库连接利用率，并说明锁超时、重建失败、旧值降级和热点切换时的行为。
