---
id: redis_distributed_lock
title: Redis 分布式锁的安全边界
domain: redis
source_type: theory
content_kind: hard_negative
tags: [redis, 分布式锁, 幂等]
aliases: [Redis 锁, owner token]
difficulty: intermediate
question_patterns:
  - Redis 分布式锁为什么必须校验唯一所有者？
  - 锁过期和业务执行超时会产生什么安全问题？
references:
  - title: Redis 分布式锁
    url: https://redis.ac.cn/docs/latest/develop/use/patterns/distributed-locks/
    source_kind: secondary_cn
    publisher: Redis 中文文档镜像
  - title: Redis 分布式锁的正确实现方式
    url: https://www.cnblogs.com/linjiqin/p/8003838.html
    source_kind: secondary_cn
    publisher: 博客园作者 Ruthless
---
# Redis 分布式锁的安全边界

## 核心结论
安全的 Redis 锁至少需要原子获取、唯一 owner token、有限租约和校验所有者后的原子释放。锁只能减少并发冲突，不能替代业务幂等，也不能天然保证外部资源不被过期持有者继续修改。

## 机制与边界
获取时使用带条件和过期时间的原子命令，值写入每次请求生成的不可预测唯一 token。释放时通过 Lua 脚本先比较 token，再删除键，避免旧持有者误删新锁。租约必须覆盖正常执行时间，并在明确所有权时续期。业务执行超过租约后，新的持有者可能同时进入；需要数据库版本、唯一约束或 fencing token 拒绝旧持有者写入。

## 常见错误
先设置键再设置过期会在中间失败时留下死锁。直接删除锁可能删除别人的新租约。固定 token 无法区分请求，盲目续期会让故障任务长期占锁。把主从故障切换后的复制延迟忽略掉，也会高估锁的安全性。

## 工程权衡
单 Redis 实例实现简单、延迟低，但故障模型有限；更复杂的多节点算法增加延迟与时钟假设。很多场景使用数据库唯一约束、状态机或幂等键更直接。只有明确互斥资源和可接受故障语义后才应选锁。

## 可观察评分信号
回答应说明 token 生成、原子获取释放、租约长度、续期停止、超时任务和进程崩溃行为，并给出获取失败率、持锁时长、续期失败、过期后写入拒绝和幂等冲突指标。
