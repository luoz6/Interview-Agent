---
id: mysql_online_migration
title: MySQL 在线表结构迁移
domain: mysql
source_type: engineering_guide
content_kind: engineering_practice
tags: [mysql, 在线迁移, ddl]
aliases: [Online DDL, 无停机迁移]
difficulty: intermediate
question_patterns:
  - 如何在不停机条件下修改大表结构？
  - 在线 DDL 和业务分批回填应如何配合？
references:
  - title: InnoDB 和在线 DDL
    url: https://mysql.net.cn/doc/refman/8.0/en/innodb-online-ddl.html
    source_kind: secondary_cn
    publisher: MySQL 中文手册镜像
  - title: InnoDB and Online DDL 读书笔记
    url: https://www.cnblogs.com/xuliuzai/p/18111648
    source_kind: secondary_cn
    publisher: 博客园作者东山絮柳仔
---
# MySQL 在线表结构迁移

## 核心结论
无停机迁移不是单条 ALTER TABLE，而是新旧代码兼容、结构变更、历史回填、数据校验和清理的分阶段发布。Online DDL 能降低部分锁影响，但仍可能消耗磁盘、日志和复制资源。

## 机制与边界
先发布能同时读写新旧结构的代码，再执行受支持的 INSTANT 或 INPLACE 变更。新增字段应先允许旧代码继续工作，回填任务按主键范围小批提交，记录游标并可幂等重跑。持续观察元数据锁、磁盘、日志、复制延迟和业务延迟。完成双读校验后切换读取，最后在独立发布中删除旧字段。

## 常见错误
看到“在线”就认为完全不加锁，会忽略准备和提交阶段的元数据锁。一次性更新全表会形成长事务和复制积压。只准备数据库回滚而没有旧代码兼容路径，发生问题时仍无法恢复服务。

## 工程权衡
原生 Online DDL 简单，但不同操作和版本支持范围不同；影子表工具控制更细，却引入触发器、复制与切换复杂度。更小批次降低风险但延长迁移时间。限速应由业务延迟和复制余量动态决定。

## 可观察评分信号
回答应给出阶段顺序、兼容窗口、批次大小、限速和停止条件，展示元数据锁等待、复制延迟、回填吞吐、校验差异和回滚演练结果。
