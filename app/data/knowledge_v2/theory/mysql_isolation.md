---
id: mysql_isolation
title: MySQL 事务隔离与锁边界
domain: mysql
source_type: theory
content_kind: hard_negative
tags: [mysql, 事务, 隔离级别]
aliases: [RC 与 RR, next-key lock]
difficulty: intermediate
question_patterns:
  - MySQL 的 RC 和 RR 在读取与加锁上有什么区别？
  - 快照读和当前读为什么会看到不同结果？
references:
  - title: InnoDB 事务隔离级别
    url: https://mysql.net.cn/doc/refman/8.0/en/innodb-transaction-isolation-levels.html
    source_kind: secondary_cn
    publisher: MySQL 中文手册镜像
  - title: MySQL 的事务
    url: https://cloud.tencent.com/developer/article/2238570
    source_kind: secondary_cn
    publisher: 腾讯云开发者社区作者
---
# MySQL 事务隔离与锁边界

## 核心结论
隔离级别决定事务读取版本与并发异常，不等于所有查询都会加相同的锁。InnoDB 中普通一致性读通常使用多版本快照，锁定读和写入读取当前版本并按索引范围加锁。

## 机制与边界
RC 通常为每条一致性读创建新快照，RR 在同一事务的普通一致性读中复用快照。当前读需要看到可修改的最新记录，会获取记录锁，并在 RR 的范围访问中可能使用 next-key lock 保护记录与间隙。是否出现间隙锁还取决于索引、唯一条件和语句类型。隔离级别不能替代业务唯一约束与显式状态检查。

## 常见错误
把 RR 简化为绝对没有幻读会忽略快照读与当前读混用。认为查询条件只返回一行就只锁一行，也会忽略缺少索引时的扫描范围。随意降低隔离级别可能减少部分锁冲突，却把一致性责任推给应用。

## 工程权衡
更强的范围保护减少并发异常，但增加锁等待和死锁机会。RC 提高部分并发性，却可能让同一事务多次读取结果变化。选择应由业务不变量驱动，并配合唯一约束、乐观版本或显式锁定读。

## 可观察评分信号
回答应区分快照读与当前读，说明索引如何决定锁范围，举例比较 RC/RR，并用锁等待、死锁、事务时长和一致性冲突指标验证选择。
