---
id: mysql_deadlocks
title: InnoDB 死锁识别与恢复
domain: mysql
source_type: theory
content_kind: failure_mode
tags: [mysql, innodb, 死锁]
aliases: [事务死锁, 锁等待环]
difficulty: intermediate
question_patterns:
  - InnoDB 死锁为什么发生，应用应如何恢复？
  - 怎样减少并发事务形成等待环？
references:
  - title: InnoDB 中的死锁
    url: https://mysql.net.cn/doc/refman/8.0/en/innodb-deadlocks.html
    source_kind: secondary_cn
    publisher: MySQL 中文手册镜像
  - title: mysql 死锁问题分析
    url: https://www.cnblogs.com/LBSer/p/5183300.html
    source_kind: secondary_cn
    publisher: 博客园作者 zhanlijun
---
# InnoDB 死锁识别与恢复

## 核心结论
死锁是多个事务各自持有资源并等待对方释放而形成的等待环。InnoDB 会检测环并回滚一个事务，应用必须把死锁当作可预期的并发结果，以有限重试和幂等语义恢复。

## 机制与边界
行锁、间隙锁、唯一性检查和不同访问顺序都可能形成环。减少死锁应让事务按一致顺序访问记录，缩短事务时间，避免用户交互和外部网络调用处于事务内，并使用合适索引减少锁定扫描范围。捕获死锁错误后先回滚整个事务，再以随机退避重试；重试次数必须有限，写操作需有幂等键或唯一约束。

## 常见错误
把死锁等同于普通锁等待会选错处理方式。只调整超时不能消除等待环，也可能让故障恢复更慢。重试单条 SQL 而不重放完整事务，会破坏业务不变量；无限立即重试会制造新的竞争。

## 工程权衡
串行化访问能降低死锁，但牺牲并发。更强索引选择性缩小锁范围，却增加索引维护成本。热点业务可使用分片、排队或乐观版本控制，但会引入冲突处理与顺序语义。

## 可观察评分信号
回答应能画出等待环，说明受害事务回滚、完整事务重试和幂等保证，并使用死锁日志、锁等待时间、重试成功率、热点键和事务时长验证改进。
