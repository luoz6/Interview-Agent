---
id: redis_consistency
title: Redis 缓存一致性的边界
domain: redis
source_type: theory
content_kind: mechanism
tags: [redis, 缓存, 一致性]
aliases: [Cache-Aside 一致性, 缓存更新顺序]
difficulty: beginner
question_patterns:
  - 缓存与数据库更新时怎样处理并发一致性窗口？
  - 为什么常见做法是更新数据库后删除缓存？
references:
  - title: Cache-Aside 模式
    url: https://learn.microsoft.com/zh-cn/azure/architecture/patterns/cache-aside
    source_kind: official_cn
    publisher: Microsoft Learn
---
# Redis 缓存一致性的边界

## 核心结论
Cache-Aside 通常以数据库为事实来源，读未命中时回填缓存，写入时先完成数据库事务再使缓存失效。它提供工程上可控的最终一致性窗口，不是跨数据库与缓存的原子事务。

## 机制与边界
读请求先查缓存，未命中再读数据库并设置过期时间。写请求更新数据库后删除缓存，使后续读取重新装载最新值。并发窗口中，旧读可能在写完成后把旧值回填，因此需要结合较短过期、版本号、条件写或重建协调。删除失败应进入可靠重试或变更事件流程，并设置上限与告警。

## 常见错误
先更新缓存再写数据库可能在数据库失败时留下错误值。先删缓存再写数据库容易被并发读回填旧值。延迟双删只能缩小部分窗口，不能替代失败补偿和版本控制。把所有请求都加分布式锁会降低可用性与吞吐。

## 工程权衡
强一致要求越高，协调成本和失败模式越复杂。商品价格等敏感读可校验版本或绕过缓存，普通展示数据可接受短时陈旧。事件驱动失效解耦写路径，但要处理重复、乱序和消费延迟。

## 可观察评分信号
回答应明确事实来源、读写顺序、最大陈旧时间、删除失败补偿和并发回填处理，并用缓存版本不一致数、重试积压、未命中率和数据库回源量验证方案。
