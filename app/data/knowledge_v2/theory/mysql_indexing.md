---
id: mysql_indexing
title: MySQL 联合索引与覆盖索引
domain: mysql
source_type: theory
content_kind: mechanism
tags: [mysql, 索引, 查询优化]
aliases: [最左前缀, 覆盖索引]
difficulty: beginner
question_patterns:
  - 如何利用联合索引和覆盖索引减少回表？
  - 联合索引列顺序应该怎样确定？
references:
  - title: MySQL 多列索引
    url: https://mysql.net.cn/doc/refman/8.0/en/multiple-column-indexes.html
    source_kind: secondary_cn
    publisher: MySQL 中文手册镜像
  - title: MySQL 的索引
    url: https://cloud.tencent.com/developer/article/2238573
    source_kind: secondary_cn
    publisher: 腾讯云开发者社区作者
---
# MySQL 联合索引与覆盖索引

## 核心结论
索引设计应从稳定的查询条件、排序和返回列出发。联合索引能按最左前缀支持多种访问路径，覆盖索引让查询直接从索引取得所需列，减少回表，但两者都要承担写入和存储成本。

## 机制与边界
B+Tree 联合索引按列顺序排序，通常先利用等值条件，再考虑范围和排序。范围条件之后的列往往不能继续缩小扫描区间，但仍可能参与索引条件过滤。选择性、查询频率和排序要求共同决定列顺序。覆盖索引包含过滤与返回所需列，可以减少随机回表；若返回列过多，索引会变宽并降低缓存效率。

## 常见错误
只记住“高选择性列放前面”会忽略实际查询前缀和排序。函数、隐式类型转换或不匹配的前缀可能让索引失效。看到使用索引不代表扫描少，还需检查 rows、过滤比例和实际执行时间。

## 工程权衡
增加索引能提升读性能，但降低写吞吐并增加页分裂、存储和维护时间。为少量慢查询建立很宽的覆盖索引未必划算。应合并重复索引，监测使用情况，并在生产数据分布上验证。

## 可观察评分信号
回答应给出查询样例、索引列顺序、最左前缀解释和执行计划，对比扫描行数、回表次数、p95 延迟和写入开销，并说明数据分布变化后的复查方法。
