# 电商数据库项目设计规范

## 分工总结

- **A 负责 MySQL。** 实现库存、购物车、订单、支付和售后，绘制 ERD，装载并校验 MySQL 测试数据，交付 Q3、Q7–Q11、Q13。
- **B 负责 MongoDB。** 实现商品属性和行为事件的存储，装载 MongoDB 测试数据，交付 Q1、Q4–Q6，并为 Q2 提供 MongoDB 查询。统一生成全组的小样本和全量测试数据。汇总最终报告和独立修订日志，整理根目录 `README.md`。
- **C 负责 Redis 和 Neo4j。** 实现会话、最近浏览缓存和共购图，交付 Q2 的查询入口和 Q12。使用同批测试数据初始化 Redis 会话并同步 Neo4j。维护统一查询入口，协调跨库联调并从空库复跑。

三人各自编写负责的报告正文，整理查询结果、性能记录、修订条目和运行说明。跨库功能通过第 6 节的接口联调。

本规范定义四库的模型、业务约束、交接接口和查询口径。第 8 节对照 Part 1 的十项要求，第 9 节规定全量数据装载、13 道查询的性能验收和最终提交。

## 1. 全组共用约定

- **共享 ID。** 用户、商品、变体、购物车和订单的业务 ID 由 A 分配。测试数据的 ID 按相同规则由 B 的生成脚本统一分配，四库复用同一批 ID。ID 为不超过 `2^53 - 1` 的正整数。MySQL 用 `BIGINT`，MongoDB 用 BSON `Int64`，Neo4j 用 `INTEGER`。Redis 和跨模块 JSON 用无前导零的十进制字符串，如 `"105"`。
- **时间。** 统一用 UTC，跨库精度为毫秒。接口使用固定格式的 RFC 3339 时间戳，保留三位小数并以 `Z` 结尾，如 `2026-01-15T14:30:00.000Z`。全组共用同一个统计截止时间 `as_of`，时间窗包含起点和终点。按本地日期或时段统计时用 `America/Chicago`。
- **类目和设备名称。** 商品类目至少包含 `fashion`、`headphones`、`home_decor`。新增类目时，先在 MySQL 的 `categories` 表登记小写名称，其他库沿用同一拼写。设备类型用 `tablet`、`laptop`、`phone`、`desktop`。
- **接口调用。** 应用层统一使用 Python，通过 Python 函数调用各库模块。各模块使用对应驱动执行 SQL、MongoDB 查询、Redis 命令或 Cypher。数据生成器使用 Python。代码注释和 README 使用英文。
- **金额和空值。** MySQL 用整数美分存金额。程序直接查库时，可以拿这个整数计算。例如，118 美元存为 `11800`。如果用 JSON 文件或接口交换数据，金额加引号，写成 `{"subtotal_cents": "11800"}`。收到后先转回整数再计算。JSON 中的可空字段没有值时写 `null`。

## 2. MySQL｜A 负责交易与库存

在 `mysql/schema.sql` 定义下列 13 张表、约束和索引。数据库名建议用 `shop`。除标为可空的字段和生成列 `active_user_id` 外，字段均为 `NOT NULL`。

### 用户与商品

| 表 | 字段与约束 | 功能 |
|---|---|---|
| `users` | `user_id BIGINT PK`，`name VARCHAR(100)`，`email VARCHAR(254) UQ`，`created_at DATETIME(6)` | 保存用户信息，供购物车归属和 Q13 使用。 |
| `categories` | `category_id BIGINT PK`，`name VARCHAR(64) NOT NULL UQ` | 登记第 1 节约定的商品类目。 |
| `products` | `product_id BIGINT PK`，`category_id BIGINT FK → categories`，`name VARCHAR(160)`，`return_window_days SMALLINT`，`restocking_fee_cents BIGINT` | 款式身份、归类、名称和退货政策。本项目建档后保持商品所属类目不变。退货期限从送达起算，补货费为同一退货明细收取的固定费用。 |
| `product_variants` | `variant_id BIGINT PK`，`product_id BIGINT FK → products`，`sku VARCHAR(64) UQ`，`unit_price_cents BIGINT`，`stock_qty INT` | 记录每个 SKU 的当前价格和库存。库存允许为零。已有交易的变体保持原 SKU 和商品归属。 |

### 购物车与配送

| 表 | 字段与约束 | 功能 |
|---|---|---|
| `carts` | `cart_id BIGINT PK`，`user_id BIGINT FK → users`，`status ENUM`，`created_at DATETIME(6)`，`updated_at DATETIME(6)`，`last_device ENUM` 可空，`item_count BIGINT DEFAULT 0`，`version BIGINT DEFAULT 1`，`active_user_id BIGINT` 生成列且 UQ | `active_user_id` 在车为 `ACTIVE` 时等于 `user_id`，其他状态为 NULL。唯一约束保证每位用户最多一辆活跃车。 |
| `cart_items` | `cart_id BIGINT FK → carts`，`variant_id BIGINT FK → product_variants`，`quantity INT`，复合 PK `(cart_id, variant_id)` | 记录每个 SKU 的正整数数量。将数量改为零时删除该行。 |
| `shipping_options` | `shipping_option_id BIGINT PK`，`name VARCHAR(64)`，`fee_cents BIGINT`，`dispatch_days SMALLINT`，`transit_days SMALLINT`，`is_enabled BOOLEAN` | `name` 是结账时显示的配送名称，例如标准配送、加急配送或隔夜配送。结账只展示启用的选项。下单后 `dispatch_days` 日预计发货，发货后 `transit_days` 日预计送达。 |

`carts.status` 取 `ACTIVE`、`CONVERTED` 或 `ABANDONED`。`last_device` 用第 1 节的设备类型，首次使用购物车前为 NULL。

### 订单、支付与售后

| 表 | 字段与约束 | 功能 |
|---|---|---|
| `orders` | `order_id BIGINT PK`，`cart_id BIGINT FK → carts UQ`，`shipping_option_id BIGINT FK → shipping_options`，`shipping_name_snapshot VARCHAR(64)`，`status ENUM`，`placed_at DATETIME(6)`，`subtotal_cents BIGINT`，`tax_cents BIGINT`，`shipping_fee_cents BIGINT`，`total_cents BIGINT`，`estimated_ship_at DATETIME(6)`，`estimated_delivery_at DATETIME(6)`，`shipped_at DATETIME(6)` 可空，`delivered_at DATETIME(6)` 可空 | 通过来源购物车关联用户。总额等于小计、税、运费之和，预计送达时间不得早于预计发货时间。 |
| `order_items` | `order_item_id BIGINT PK`，`order_id BIGINT FK → orders`，`variant_id BIGINT FK → product_variants`，`quantity INT`，`product_name_snapshot VARCHAR(160)`，`unit_price_cents BIGINT`，`return_window_days SMALLINT`，`restocking_fee_cents BIGINT`，UQ `(order_id, variant_id)` | 购买数量、成交价、商品名和当时适用的退货政策。订单小计等于购买明细金额之和。 |
| `payments` | `payment_id BIGINT PK`，`order_id BIGINT FK → orders UQ`，`method ENUM`，`provider_token VARCHAR(160)`，`amount_cents BIGINT`，`approved_at DATETIME(6)` | 本项目每笔确认订单约定一条已获批支付。支付方式为 `CARD/BANK_ACCOUNT`，支付金额与订单总额相同。样例使用支付方风格的模拟 token，数据库禁止保存完整卡号。 |
| `returns` | `return_id BIGINT PK`，`order_item_id BIGINT FK → order_items UQ`，`quantity INT`，`resolution ENUM`，`status ENUM`，`reason VARCHAR(255)`，`requested_at DATETIME(6)`，`updated_at DATETIME(6)`，`return_shipping_method VARCHAR(24)` 可空，`return_label_ref VARCHAR(160)` 可空，`return_tracking_code VARCHAR(64)` 可空，`estimated_resolution_at DATETIME(6)` 可空 | 保存退回申请、原订单明细、退回件数、处理类型、进度和寄回信息。退货标签引用可供界面打印。 |
| `return_refunds` | `return_id BIGINT PK, FK → returns`，`refund_amount_cents BIGINT` 可空，`restocking_fee_cents BIGINT DEFAULT 0` | 保存实际扣除的补货费和扣费后的净退款额。退款完成前，净退款额留空。 |
| `return_exchanges` | `return_id BIGINT PK, FK → returns`，`exchange_variant_id BIGINT FK → product_variants NOT NULL`，`shipped_at DATETIME(6)` 可空 | 保存要换的新 SKU。替换商品寄出后记录发货时间。 |

### 约束与索引

- 在 `mysql/schema.sql` 声明表中列出的主键、外键、唯一约束和生成列。用 `CHECK` 限制件数为正、库存、金额和配送天数为非负。在 `orders` 检查总额等于小计、税费和运费之和，预计送达不得早于预计发货。
- 在同一文件建立初始索引 `carts(user_id, status)`、`carts(status, cart_id)`、`carts(created_at)`、`product_variants(product_id, variant_id)`、`product_variants(stock_qty)`。每组完全相同的索引建一次，运行查询后用 `EXPLAIN` 复核。

### ERD

将含全部字段、类型、主外键和关系基数的乌鸦脚 ERD 保存在 `mysql/erd.mmd`，把渲染图放入报告 Part 1 的关系型设计部分。

下图仅展示表间关系。每条退换申请恰好关联一种处理明细。

~~~mermaid
erDiagram
    USERS ||--o{ CARTS : owns
    CATEGORIES ||--o{ PRODUCTS : classifies
    PRODUCTS ||--|{ PRODUCT_VARIANTS : has
    CARTS ||--o{ CART_ITEMS : holds
    PRODUCT_VARIANTS ||--o{ CART_ITEMS : selected
    CARTS ||--o| ORDERS : converts
    SHIPPING_OPTIONS ||--o{ ORDERS : selected
    ORDERS ||--|{ ORDER_ITEMS : contains
    PRODUCT_VARIANTS ||--o{ ORDER_ITEMS : purchased
    ORDERS ||--|| PAYMENTS : paid
    ORDER_ITEMS ||--o| RETURNS : returned
    RETURNS ||--o| RETURN_REFUNDS : refunded
    RETURNS ||--o| RETURN_EXCHANGES : exchanged
    PRODUCT_VARIANTS ||--o{ RETURN_EXCHANGES : replacement
~~~

### 写入事务

在 `mysql/transactions.py` 放 A 维护的 MySQL 写入函数或演示脚本。跨表检查和相关写入在同一个事务内完成。

- **使用购物车。** 用户打开或恢复活跃购物车时，更新 `last_device` 和 `updated_at`。修改商品时，用 `SELECT ... FOR UPDATE` 锁住活跃购物车并更新 `cart_items`，在同一事务写回明细件数之和 `item_count`，更新设备和时间并递增 `version`。打开或恢复购物车只记录访问，沿用当前版本。修改商品提交后按第 3 节记录行为事件，结账时才扣减库存。
- **预览结账。** 按当前 SKU 单价计算商品小计，列出启用的配送选项。选择配送后展示商品小计、税费、运费、应付总额和预计发货及送达时间，供用户确认。
- **结账。** 锁定购物车，已有订单时返回原订单。否则检查购物车活跃、明细非空且版本与用户确认时一致。版本变化时返回最新购物车供用户重新确认。按固定 SKU 顺序锁定库存行并读取单价，检查配送选项仍启用，用当前商品小计、运费和传入的税费重算总额。配送已停用或金额与用户确认值不同时，返回最新结账信息供重新确认。用 `stock_qty >= quantity` 条件扣减库存并执行本地模拟支付。库存不足或支付失败时回滚。成功时写入 `orders`、`order_items`、`payments`，核对订单小计和支付金额，再将购物车标为 `CONVERTED`、更新版本并提交。
- **更新订单状态。** 按 `CONFIRMED → SHIPPED → DELIVERED` 转换，并保存实际发货和送达时间。若演示取消，允许从 `CONFIRMED` 转为 `CANCELLED`，在同一事务恢复库存，购物车保留已转化记录。
- **申请退换货。** 检查订单已送达且仍在退货期限内，退回件数在 1 到购买件数之间。在同一事务写入 `returns` 和唯一一条匹配明细。类型为 `REFUND` 时写 `return_refunds`，类型为 `EXCHANGE` 时写 `return_exchanges`。
- **完成退换货。** 主表状态走 `REQUESTED → IN_TRANSIT → RECEIVED → COMPLETED`，另有 `REJECTED`。锁定申请并确认状态为 `RECEIVED` 后处理。退款明细记录净退款额和实际补货费。净额为退回件数乘成交单价减补货费。补货费取订单明细中的政策快照，上限为退回商品成交金额。换货选同款另一 SKU，确认寄出时扣减新 SKU 库存并记录发货时间。明细、库存变化和 `COMPLETED` 状态一起提交。入库流程判断退回品能否重新销售。

### A 的交付清单

**给 B、C 的只读接口。** 在 `mysql/interfaces.py` 提供以下 Python 函数。对应的 SQL 文件放在 `mysql/`，用接口名命名。

- `product_catalog`（B、C 共用）。返回全部商品，每款一行，字段为 `product_id`、`name`、`category_name`。名称分别取自 `products.name` 和 `categories.name`。B 用它同步 MongoDB 商品目录，C 用它构建 Neo4j 商品和类目节点。
- `product_variants_by_ids(product_ids)`（给 B）。输入一批商品 ID，每个匹配变体返回一行，字段为 `product_id`、`variant_id`、`sku`、`unit_price_cents`、`stock_qty`。空输入返回空数组。B 在 Q4 用 `variant_id` 匹配规格和库存。
- `purchased_order_products`（给 C）。返回已支付且未取消的订单商品，字段为 `order_id`、`product_id`、`quantity`。同一订单内同款商品的不同 SKU 按数量合计为一行。C 用它构建 Neo4j 的 `CONTAINS` 边，与商品目录使用同一次 MySQL 读取快照。
- `active_cart_by_user_id(user_id)`（给 C）。输入已验证会话的 `user_id`，返回对应 `ACTIVE` 购物车的 `cart_id`，没有活跃车时返回 `null`。C 用它从不同设备的会话找到同一辆购物车。

**给助教运行的 MySQL 查询。** 在 `queries/` 放 Q3、Q7、Q8、Q9、Q10、Q11、Q13 的 SQL 文件，如 `q03.sql`。在各文件开头按第 7 节标明参数，由 C 接入统一入口。

## 3. MongoDB｜B 负责商品属性与事件

在 `mongo/schema.py` 使用 PyMongo 创建 `catalog` 和 `events`，配置字段验证和索引。数据库名建议用 `shop_catalog`。

### 商品目录 `catalog`

每款商品存一份文档，用 `product_id` 对应 MySQL 商品。

| 字段 | 类型与用途 |
|---|---|
| `_id` | ObjectId，MongoDB 内部键。 |
| `product_id` | Int64，唯一。与 MySQL 同一个商品。 |
| `name`、`category_name` | String，从 MySQL 同步。两者分别是商品名称和类目名称。 |
| `attributes` | Object，存同一款商品的各变体共用的属性，按类目验证字段。服装 `material` String。耳机 `battery_minutes` Int32、`connectivity` String 数组、`weight_g` Int32。家居装饰 `ceramic_type` String、`care_instructions` String、`weight_g` Int32。 |
| `variants` | Array，每项含 `variant_id`（Int64）和 `attributes`（Object）。规格随类目变化。服装用 `color` 和 `size_code`（String）。花瓶按尺寸区分变体时，可用 `width_mm`、`height_mm` 和 `depth_mm`（Int32，单位毫米，正整数）。耳机有需要时也可用 `color`。 |

没有区别规格的商品也有一个可售变体，规格填 `{}`。服装用小写颜色名和大写尺码。Q4 的蓝色包含 `blue` 和 `aqua-blue`，大码用 `L`。同款花瓶各变体的尺寸相同时，尺寸放在商品级 `attributes`。尺寸随变体变化时，放在各变体的 `attributes`。

### 行为事件 `events`

每次行为存一条事件，用于查浏览历史、搜索词频、点击和停留时长。

| 字段 | 类型与用途 |
|---|---|
| `_id` | ObjectId，事件唯一键。 |
| `user_id`、`event_type`、`occurred_at`、`device` | Int64、String、Date、String，必填。设备值与 SQL 保持一致。时间是行为实际发生时点。 |
| `product_id` | Int64，商品页浏览和点击时必填。 |
| `search_term` | String，搜索时必填。统一去首尾空白和转小写后再按词统计。 |
| `duration_ms` | Int32，可空，浏览页离开时可按原事件 ID 补写，最小值为 0。 |
| `cart_id`、`variant_id`、`cart_action` | Int64、Int64、String，购物车行为时必填。动作取 `ADD/SET_QUANTITY/REMOVE`。 |

### 约束与索引

- 在 `mongo/schema.py` 按上表检查 BSON 类型、必填字段与值域。`catalog.product_id` 建唯一索引，`(category_name, product_id)` 建目录索引。
- `events.event_type` 取 `VIEW`、`SEARCH`、`CLICK` 或 `CART_ACTION`。按事件类型验证必填字段。为 `events` 建 `(user_id, event_type, occurred_at)` 索引支持 Q2、Q6，建 `(event_type, product_id)` 索引支持 Q5。

### 数据写入与同步

在 `mongo/write.py` 放 B 维护的商品同步和事件写入代码。

- **同步商品。** 调用 A 的 `product_catalog`，按 `product_id` 同步 `name` 和 `category_name`。新商品建文档时同时写入属性和变体规格。已有商品的属性和规格由 B 的写入流程单独维护。
- **写入变体规格。** 用 A 的 `product_variants_by_ids(product_ids)` 核对每款商品在两库的变体 ID 集合一致。新增 SKU 后同步补齐对应规格。保存前检查同款商品内的变体 ID 和规格组合各自唯一。已经用于交易的变体沿用原规格。
- **记录行为。** 打开商品页写 `VIEW`，点击商品链接写 `CLICK`，提交搜索写 `SEARCH`。上报前生成 `_id`，重试复用同一个 ID。写入端按 `_id` 保留一条事件。离开商品页时按原 `VIEW` 的 ID 补写 `duration_ms`。MySQL 成功更新购物车后再写 `CART_ACTION`。新 `VIEW` 写入成功后，由应用调用 C 的 `invalidate_recent_views(user_id)`。

### B 的交付清单

**给 C 的只读接口。** 在 `mongo/interfaces.py` 提供 `recent_views(user_id, as_of)`，供 C 在 Q2 缓存未命中时调用。输入用户 ID 和统计截止时间，按第 7 节口径返回有序数组，每项含 `product_id`（Int64）、`name`（String）、`last_viewed_at`（Date）。没有记录时返回空数组。

**给助教运行的查询。** 在 `queries/` 放 `q01.py`、`q04.py`、`q05.py`、`q06.py`，在文件开头按第 7 节标明参数，由 C 接入统一运行入口。

- Q1、Q5、Q6 直接查 MongoDB，返回结果按第 7 节。
- Q4 的入口放在 `queries/q04.py`。先按第 7 节筛选候选变体，每行含 `product_id`、`variant_id` 和 `name`，再按候选商品 ID 批量调用 A 的 `product_variants_by_ids(product_ids)`。匹配同一商品的同一变体，再按第 7 节的库存条件和去重规则返回结果。

**报告。** 在 Part 1 比较 MongoDB 文档和 MySQL JSON 列的类目属性建模方式，说明选择 MongoDB 的理由、变体如何对应 MySQL 库存，以及事件如何采集和查询。选型表和报告的交付分工见第 8、9 节。

## 4. Redis｜C 负责会话和最近浏览缓存

在 `redis/keys.md` 记录以下键的结构、字段类型和过期时间。在 `redis/interfaces.py` 实现会话与缓存操作。购物车和最后使用设备保存在 MySQL，会话过期后可重新登录并恢复活跃购物车。

### 键和值

| 键 | 值和生命周期 | 功能 |
|---|---|---|
| `session:{session_id}` | JSON 字符串，`user_id`（十进制字符串）、`device`（第 1 节约定的设备类型）、`issued_at` 和 `last_seen_at`（UTC 字符串）。30 分钟空闲 TTL。 | 每个设备登录时建立独立会话。 |
| `recent_views:{user_id}` | JSON 字符串，含 `as_of`（UTC 时间字符串）和 `items`（最多五项的数组）。每项含 `product_id`（十进制字符串）、`name`（商品名称字符串）和 `last_viewed_at`（UTC 时间字符串）。5 分钟 TTL。 | 缓存用户在指定统计时点的最近浏览结果。查询无结果时缓存空数组。 |

### 调用的接口

- **A 的 `active_cart_by_user_id(user_id)`。** 从有效会话取 `user_id` 后调用，让同一用户的不同设备找到同一辆 MySQL 活跃车。调用结果见第 2 节。
- **B 的 `recent_views(user_id, as_of)`。** Q2 在 Redis 无匹配缓存时调用。B 的返回格式见第 3 节。C 按第 1 节转换商品 ID 和时间后写入 Redis。

### 提供的接口

会话与缓存操作函数放在 `redis/interfaces.py`，Q2 查询入口放在 `queries/q02.py`。

- `create_session(user_id, device)`。应用确认登录成功后调用，写入会话并返回随机生成的 `session_id`。
- `read_session(session_id)`。应用用请求中的会话 ID 调用。在一次原子操作中检查会话键、更新 `last_seen_at` 并按上表续期。成功时返回会话数据，键不存在时返回 `null`。
- `end_session(session_id)`。退出登录时删除当前设备的会话。
- `invalidate_recent_views(user_id)`。供 B 的事件写入流程在新 `VIEW` 保存成功后调用，用 `DEL` 清除该用户的缓存。重载数据集时清理最近浏览缓存。
- `q02(sarah_user_id, as_of)`。入口脚本放在 `queries/q02.py`，供统一运行入口调用。先用 `GET` 查缓存，命中且 `as_of` 相同就返回数组。否则调用 B 的 `recent_views`，再用 `SET ... EX 300` 回填并返回结果。空数组也是有效缓存结果。

## 5. Neo4j｜C 负责共购图

在 `neo4j/schema.cypher` 为 `Order.order_id`、`Product.product_id` 和 `Category.name` 创建唯一约束。

### 节点与关系

| 节点或关系 | 属性与唯一性 |
|---|---|
| `(:Order)` | `order_id` Integer，唯一。以 MySQL 已确认、发货或送达的已支付订单构图。 |
| `(:Product)` | `product_id` Integer，唯一，`name` String。按款式建节点。 |
| `(:Category)` | `name` String，唯一，取 MySQL 中的类目名称。 |
| `(:Order)-[:CONTAINS]->(:Product)` | `quantity` Integer。在同一订单中把同款多个 SKU 的购买数量相加，按订单与商品保留一条边。 |
| `(:Product)-[:IN_CATEGORY]->(:Category)` | 每款商品一条主类目关系。 |

### 调用的接口

在 `neo4j/interfaces.py` 放同步代码，从同一次 MySQL 读取快照调用 A 的两个只读接口。

- `product_catalog` 用于建立商品、类目节点及 `IN_CATEGORY` 关系。
- `purchased_order_products` 用于建立订单节点及 `CONTAINS` 关系。返回字段见第 2 节。

### 提供的接口

- `sync_purchase_graph()` 放在 `neo4j/interfaces.py`，供初始化和定期刷新调用。按业务键用 `MERGE` 创建或更新节点和关系，按快照写入主类目和购买数量。全量数据读取并写入成功后，再清理不在有效快照内的订单和失效的关系。同一快照可重复同步，成功后记录同步时间。
- Q12 的 Cypher 放在 `queries/q12.cypher`，供统一运行入口调用。读取完整同步后的图快照，查询口径见第 7 节 Q12。

## 6. 跨库接口和同步

接口的输入和返回字段见第 2 至 5 节。由应用入口组装跨库调用，共享 ID 按第 1 节转换。

### 数据写入与同步

| 数据 | 来源 | 目标 | 写入或同步时机 | 负责人 |
|---|---|---|---|---|
| 用户和交易数据 | 应用的用户、购物车、结账和售后操作 | MySQL | 业务操作成功时提交。结账在同一事务读取并扣减当前库存。 | A |
| 商品目录 | MySQL 的商品 ID、名称和类目 | MongoDB 的 `catalog` | 初次装载后同步，之后计划每 5 分钟刷新一次。已有商品按 ID 更新名称和类目，规格由 B 的写入流程维护。 | A 提供目录数据，B 执行同步。 |
| 商品属性和规格 | 应用的商品维护流程 | MongoDB 的 `catalog` | 新建商品文档或修改属性、规格时写入。写入变体规格前核对 MySQL 的变体归属。 | B |
| 行为事件 | 应用的浏览、搜索、点击和购物车操作 | MongoDB 的 `events` | 浏览、搜索和点击发生时写入。购物车事件等 MySQL 更新成功提交后再写。 | B |
| 各设备的登录会话 | 应用的登录验证结果 | Redis | 登录成功时创建，有效操作时续期，退出时删除。空闲 30 分钟后过期。 | C |
| Q2 最近浏览结果 | MongoDB 的浏览记录与商品目录 | Redis | Q2 无匹配缓存时回填，有效期 5 分钟。新浏览事件写入成功后清除该用户的缓存，重载数据集时清理最近浏览缓存。 | B 提供查询和新浏览事件，C 负责回填和清除。 |
| 共购图 | 同一次 MySQL 读取快照中的商品目录及已支付、未取消订单的商品明细 | Neo4j | 初次装载后同步，之后计划每 5 分钟刷新一次。全量写入成功后再清理已失效的订单和关系。 | A 提供快照数据，C 执行同步。 |

图中的跨库箭头表示应用层读取来源、再写入目标。虚线表示缓存删除的触发关系。

~~~mermaid
flowchart LR
    APP[应用] -->|A 提交用户和交易数据| SQL[MySQL]
    APP -->|B 写入商品规格和行为事件| DOC[MongoDB]
    APP -->|C 管理登录会话| KV[Redis]
    SQL -->|B 同步目录名称和类目| DOC
    DOC -->|C 回填 Q2 最近浏览结果| KV
    DOC -.->|新浏览触发 C 清除缓存| KV
    SQL -->|C 同步商品和有效订单| GRAPH[Neo4j]
~~~

### 查询时跨库读取

- **跨设备购物车。** 打开购物车时，C 从 Redis 会话取已验证的用户 ID 和设备，再读 MySQL 活跃购物车。应用调用第 2 节 A 的购物车写入逻辑记录访问设备。
- **Q4 查规格和库存。** B 先读 MongoDB 候选规格，再批量读 MySQL 当前库存。合并方式见第 3 节。

### 同步失败与延迟

目录或共购图同步失败时，B 或 C 保留上次成功同步时间并重跑。共购图刷新时暂停 Q12，直到全部同步成功再开放查询。缓存删除失败或遇到并发回填时，旧结果最多保留一个缓存周期，到期后从 MongoDB 重建。

目录和共购图按上表的间隔刷新。报告记录实际同步完成时间和耗时，并标明 Q12 使用的图快照。跨库查询从发出请求到取得完整结果计时，包含各库读取和应用合并，性能记录见第 9 节。

## 7. 13 道查询的交付口径

13 道作业查询的脚本统一放在 `queries/`，按 `q01` 至 `q13` 命名，扩展名对应所用语言。跨库查询的入口也放在此处，调用各数据库目录提供的接口。各题负责人按下表分配。

涉及 Sarah 的查询使用同一个 `sarah_user_id`。含 `as_of` 的查询把它作为统计截止时间。近 30 天按 UTC 从 `as_of` 往前推 30 天，近六个日历月按 UTC 往前推六个月。目标月无对应日时取月末，时间窗两端都计入。复跑用同一批数据库快照和同一个 `as_of`，订单、购物车、退换状态和共购图读取该快照中的值。

| 查询 | 负责人 | 数据库 | 参数 | 返回结果 | 统计口径 |
|---|---|---|---|---|---|
| Q1 服装规格 | B | MongoDB | 无 | 每款商品一行。返回 `product_id`、`name`、`material` 和 `variants`。变体列表含 `variant_id`、`color`、`size_code`。 | 只查 `fashion`，返回商品的全部变体，包含缺货变体。 |
| Q2 最近浏览 | C 负责查询入口，B 提供 MongoDB 查询 | Redis、MongoDB | `sarah_user_id`、`as_of` | 最多五款商品，每款一行。返回 `product_id`、`name`、`last_viewed_at`。没有结果时返回空列表。 | 统计 Sarah 近六个日历月的 `VIEW`，每款商品取最后一次浏览时间。按时间降序、商品 ID 升序取前五款。Redis 命中时返回同一统计截止时间的结果，未命中时从 MongoDB 回填。 |
| Q3 低库存变体 | A | MySQL | 无 | 每个变体一行。返回 `product_id`、`variant_id`、`sku`、`stock_qty`。 | 查所有类目中当前库存少于 5 件的变体，包含库存为 0 的变体。 |
| Q4 指定规格且有货的服装 | B | MongoDB、MySQL | 无 | 每款符合条件的商品一行。返回 `product_id`、`name`，名称取自 MongoDB 目录。 | 在 `fashion` 中按第 3 节的颜色和尺码取值筛选蓝色或 L 码。至少有一个符合规格的同一变体在 MySQL 的库存大于 0 才返回该商品。按商品去重。 |
| Q5 商品浏览量 | B | MongoDB | `as_of` | 每款目录商品一行。返回 `product_id`、`name`、`view_count`。 | 每条截至 `as_of` 的 `VIEW` 计一次，零次浏览的商品返回 0。按次数降序、商品 ID 升序排列。 |
| Q6 搜索词频与时段 | B | MongoDB | `sarah_user_id`、`as_of` | 每个搜索词一行。返回 `search_term`、`total_count`、`night_count`、`morning_count`、`afternoon_count`、`evening_count`。 | 筛选 Sarah 近 30 天的 `SEARCH`，搜索词去首尾空白并转小写后分组。将事件时间转为 `America/Chicago` 后按下方时段计数。某时段无记录时计 0，总次数等于四个时段之和。 |
| Q7 活跃购物车 | A | MySQL | 无 | 每辆活跃车一行。返回 `cart_id`、`last_device`、`item_count`、`subtotal_cents`。 | 查快照中状态为 `ACTIVE` 的购物车。件数直接读取 `carts.item_count`。购物车总额取结账前商品小计，按当前 SKU 单价乘数量后求和，税费和运费在结账时确定。空车件数和小计为 0，首次使用购物车前 `last_device` 为 NULL。 |
| Q8 Sarah 的订单 | A | MySQL | `sarah_user_id`、`as_of` | 每条订单明细一行。返回 `order_id`、`order_item_id`、`product_name_snapshot`、`sku`、`quantity`、`unit_price_cents`、`payment_method`、`shipping_name_snapshot`、`order_status`。 | 查 Sarah 的全部订单，按 `placed_at <= as_of` 筛选，包含取消订单。商品名、成交单价和配送名称取下单时保存的值。 |
| Q9 Sarah 的退换货 | A | MySQL | `sarah_user_id`、`as_of` | 每条退换申请一行。返回 `return_id`、`order_item_id`、`product_name_snapshot`、`sku`、退回件数 `quantity`、`resolution`、`status`、`refund_amount_cents`、`restocking_fee_cents`。 | 查 Sarah 的退换申请中平台已收到退回品的记录，状态取 `RECEIVED` 或 `COMPLETED`，且 `requested_at <= as_of`。按 `return_id` 左连接退款明细。换货的净退款额为 NULL，补货费为 0。退款未完成时净退款额也为 NULL。 |
| Q10 平均购买间隔 | A | MySQL | `sarah_user_id`、`as_of` | 一行 `average_days_between_purchases`，单位为天。 | 只统计 Sarah 的有效购买。有效购买为 `payments.approved_at <= as_of` 且状态未取消的订单。按获批时间排序，用 `America/Chicago` 的日历日期计算相邻购买的天数差，再取平均。同日间隔为 0，不足两笔有效购买时返回 NULL。 |
| Q11 购物车未转化率 | A | MySQL | `as_of` | 一行，返回 `cart_count`、`unconverted_cart_count`、`unconverted_pct`。 | 分母是近 30 天创建的购物车数。分子是其中没有关联订单满足 `placed_at <= as_of` 的购物车数。取消的关联订单也算曾转化。百分比为分子除以分母再乘 100，分母为 0 时返回 NULL。 |
| Q12 耳机共购商品 | C | Neo4j | 无 | 最多三行，每款商品一行。返回 `product_id`、`name`、`order_count`。 | 在已同步的图快照中从 `headphones` 找同单商品。候选商品可为另一款耳机，商品 ID 必须与触发查询的耳机不同。每个候选商品在同一订单只计一次。按订单数降序、商品 ID 升序取前三。 |
| Q13 用户订单数与最近购买 | A | MySQL | `as_of` | 快照中每位用户一行。返回 `user_id`、`order_count`、`days_since_last_purchase`。 | 按 `placed_at <= as_of` 统计全部订单，包含取消订单。最近购买沿用 Q10 的有效购买定义，计算最近付款日到截止日的 `America/Chicago` 日历天数。无订单时订单数为 0，无有效购买时天数为 NULL。 |

Q6 的本地时段按左闭右开划分。夜间为 00:00 至 06:00，上午为 06:00 至 12:00，下午为 12:00 至 18:00，晚间为 18:00 至次日 00:00。

## 8. Part 1 要求与分工

表中的「报告」指最终报告的 Part 1，仓库路径用于提交相应代码、配置或图源。设计细节按「对应章节」查看。

| 作业要求 | 负责人 | 要交什么 | 放在哪 | 对应章节 |
|---|---|---|---|---|
| 1. 为功能选数据库 | B 汇总，A、C 各写本模块的选型理由 | 功能选型表，四列为功能、数据库类型、产品、理由。覆盖商品规格、花瓶尺寸对照、SKU 库存、最近浏览与行为、跨设备购物车和会话、费用与配送选项、支付与订单确认、预计到货、退换货路径及进度、共购。 | 报告「数据库选型」 | 第 2 至 5 节的模块设计和第 6 节数据流 |
| 2. 关系型 ERD | A | 与建表 SQL 一致的完整乌鸦脚 ERD。 | 报告「关系型设计」放渲染图。仓库放 `mysql/erd.mmd` 和 `mysql/schema.sql`。 | 第 2 节的表定义及「ERD」 |
| 3. 多样属性和变体 | B 主写，A 定义 SKU 和库存 | 比较至少两种属性建模方案，说明选型理由和按 SKU 跟踪库存的做法。 | 报告「商品属性和变体」。模型放 `mongo/schema.py` 和 `mysql/schema.sql`。 | 第 2 节「用户与商品」，第 3 节「商品目录」及报告说明 |
| 4. 跨设备会话和购物车 | C 主写，A 提供活跃购物车查询和访问记录逻辑 | 说明每台设备如何建立会话，以及换设备或重新登录后如何找到同一辆活跃购物车。 | 报告「跨设备会话和购物车」。会话流程放 `redis/`，购物车查询放 `mysql/`。 | 第 2 节「A 的交付清单」，第 4 节会话接口，第 6 节「查询时跨库读取」 |
| 5. 反规范化 | A | 说明 `carts.item_count` 对读取、写入、一致性和存储的影响，以及发现差异时怎么用明细重算。 | 报告「反规范化」。字段定义放 `mysql/schema.sql`，更新逻辑放 `mysql/`。 | 第 2 节「购物车与配送」「写入事务」，第 7 节 Q7 |
| 6. 数据流 | C | 跨库数据流图，附数据方向、同步时点、数据新鲜度、查询延迟和失败重试说明。 | 报告「跨库数据流」，渲染第 6 节已有的图。 | 第 6 节「数据写入与同步」「同步失败与延迟」 |
| 7. 挑战与支付安全 | A | 说明并发扣库存与同步失败的处理方式。参照题目指定的 PCI DSS，说明如何按第 2 节仅存支付服务方令牌、避免保存完整卡号。 | 报告「挑战与支付安全」。支付表和交易实现分别放 `mysql/schema.sql`、`mysql/`。 | 第 2 节支付表和「写入事务」，第 6 节「同步失败与延迟」 |
| 8. 内存型存储 | C | 说明会话和最近浏览为何适合用 Redis，以及失效、回填和过期如何控制缓存的新鲜度。 | 报告「内存型存储」。键定义放 `redis/keys.md`，应用逻辑放 `redis/`。 | 第 4 节「键和值」「提供的接口」，第 6 节同步规则 |
| 9. 用户行为追踪 | B | 说明行为事件的采集时机、结构和索引，明确如何记录商品页停留时长。 | 报告「用户行为追踪」。集合与索引放 `mongo/schema.py`，事件写入放 `mongo/`。 | 第 3 节「行为事件」「约束与索引」「数据写入与同步」 |
| 10. 图模型 | C | 交付可运行的 Neo4j 图模型，说明节点、关系、属性和约束。用 Q12 说明何时从少量起点沿关系查找同单商品可能比多次关系型连接更高效，同时考虑候选订单量和路径范围。 | 报告「图模型」。约束放 `neo4j/schema.cypher`，同步放 `neo4j/`，查询放 `queries/q12.cypher`。 | 第 5 节图模型和同步，第 7 节 Q12 |

## 9. 实现顺序和交付责任

各人按第 2 至 5 节和第 7 节的分工实现功能与查询，跨库功能按第 6 节的接口联调。三人从开始就记录各自的重大设计改动，日志格式见本节「最终提交」。

### 各阶段分工

| 步骤 | A | B | C | 并行条件与完成标志 |
|---|---|---|---|---|
| Step 1. 确认约定和小样本 | 给出 MySQL 字段、约束和第 2 节只读接口的示例结果。 | 根据各模块的字段约定，用统一脚本准备联调小样本，并给出第 3 节 MongoDB 查询的示例结果。 | 给出 Redis 会话初始化所需字段，核对第 4、5 节依赖的接口，记录四库版本，准备统一运行入口的结构。 | 全组按第 1 节确认共享格式，按第 7 节确认题目参数，并用同一小样本的 Sarah ID、`as_of` 和预期结果联调。 |
| Step 2. 并行开发 | 按第 2 节实现 MySQL 表、事务、只读接口和查询，绘制 ERD。优先让 B、C 能读取小样本。 | 按第 3 节实现 MongoDB 集合、写入和同步、查询及给 C 的接口。 | 按第 4、5 节实现 Redis 会话与 Q2、Neo4j 同步与 Q12，在 `queries/run_queries.py` 接入 `queries/` 中的查询。 | 三人并行实现各自的模块，并各自撰写第 8 节分配的报告正文。依赖尚未接通时用约定的样例结果独立验证。接口变更时更新规范并通知使用方。 |
| Step 3. 小样本联调 | 装载 MySQL 小样本并提供真实接口，核对库存、活跃购物车和有效订单的结果。 | 同步 MongoDB 目录并装载规格和事件，和 A 验证 Q4，和 C 验证 Q2。 | 初始化 Redis 会话并接通缓存，从 MySQL 同步 Neo4j，验证 Q12 和统一入口。 | 四库使用 B 生成的同一批小样本。按下文顺序联调，用统一入口跑完 13 题，并对照预期结果。 |
| Step 4. 生成、装载全量数据并测量 | 装载并校验 B 交付的 MySQL 基础与交易数据。测量第 7 节归 A 的查询。 | 用统一脚本生成并交付全量测试数据，装载 MongoDB 的商品规格和行为事件。测量第 7 节归 B 的查询，协助 C 核对 Q2 回填。 | 根据同批会话清单初始化 Redis，从已装载的 MySQL 同步 Neo4j。测量 Q2、Q12。 | B 交付全量数据后，先装载 MySQL，再按第 6 节同步其他库。四库就绪后，三人在同一批数据上测量，各自整理所负责题目的查询结果和性能记录。 |
| Step 5. 提交前核对 | 核对 MySQL 交付和运行说明。 | 统一格式并合并最终报告，汇总独立修订日志，整理根目录 `README.md`。 | 按 README 从空库装载并用统一入口复跑，整理提交的仓库。 | 三人各自提交负责的报告正文、查询结果、性能记录、修订条目和运行说明。汇总后一起核对报告、修订日志和仓库。 |

### Step 3 的联调顺序

1. **先接通 Q2 和 Q4。** A 装载基础数据后，B 同步目录、装载商品规格与事件。B 和 C 验证 Q2 的回填、缓存命中及新浏览后的失效。A 和 B 验证 Q4 的规格与有货库存来自同一变体。两题按第 7 节核对输出。
2. **再接通跨设备购物车和 Q12。** A 和 C 验证两个设备会话能找到同一辆活跃购物车，并在打开或修改时更新最后使用设备。C 从同一次 MySQL 快照同步商品和订单，验证 Q12 的结果。MySQL 数据就绪后，图同步和 MongoDB 装载可以并行。最后用统一入口复跑全部 13 题。

### 测试数据与性能验收

- **统一生成。** B 在 `generator/generate.py` 维护统一脚本，生成联调用小样本和性能测试用全量数据，批次信息写入 `data/manifest.json`。脚本按同一批 ID 和时间线产出 MySQL 基础与交易数据、MongoDB 商品规格与行为事件，以及 Redis 会话初始化所需的用户和设备清单。每批记录随机种子、`as_of` 和 `sarah_user_id`。生成后校验跨库引用，例如每条购物车事件的用户与购物车归属一致、涉及的 SKU 存在。Neo4j 由 C 从该批 MySQL 数据同步。

- **数据规模。** 按作业 Part 3 生成并装载至少 10,000 名用户、5,000 款商品、100,000 笔订单和 500,000 条用户事件。时间戳合理分布在截至生成日的过去 12 个月内，符合从购物车到下单、配送、退货的先后顺序。按第 1、3 节覆盖不同类目的商品属性和变体，并包含购物车、会话和行为记录。四类数据库都要有数据，并各用于至少一道查询。
- **Sarah 的数据。** B 在同一批数据中生成名为 Sarah 的用户、过去 12 个月内至少 5 笔订单（至少一笔含耳机，至少两笔属于 Q10 的有效购买）、至少 1 条符合 Q9 口径的退货记录、近 6 个月的商品浏览和近 30 天的搜索记录，并准备同一辆活跃购物车以及至少两台设备的会话和操作记录。A 和 C 联调时验证 Sarah 能用两台设备操作这辆车。时间窗按第 7 节约定。
- **性能记录。** 在全量数据上实际运行第 7 节全部 13 题。各题负责人记录结果、三次耗时的中位数和可获取的执行计划。跨库题按第 6 节的端到端口径计时。Q2 的三次测试使用同一缓存条件并注明命中或未命中。目标是每道题在 2 秒内完成。超出时定位瓶颈，记录优化方案和调整后的复测耗时。

### 最终提交

1. **一份 PDF 或 Word 报告。** 由 B 按 Step 5 汇总，在正文对应位置放图、查询、结果、性能记录和设计理由。写明 A、B、C 各自的贡献。引用用到的外部资料并说明学到了什么、是否认同或如何调整。题目允许使用 AI。样例数据可自由使用 AI 生成，设计和代码建议由组员独立完成。报告引用所有用过的 AI 工具并注明用途。用 AI 做头脑风暴或辅助设计决策时，显式引用相关交互并评估哪些建议被采用、修改或舍弃。
2. **一份独立修订日志。** B 汇总三人记录的改动，日志包含至少 5 项实际发生的重大设计改动。每项写明日期、涉及的设计部分、改了什么和原因。
3. **GitHub 仓库链接或 ZIP。** 包含第 2 至 5 节的四库定义、B 维护的统一数据生成脚本、第 7 节 `queries/` 中的 13 道查询和跨库应用代码。B 把各成员提供的运行说明整理到根目录 `README.md`，写明实际数据库产品及版本、测试数据生成、初始化、导入、同步和运行步骤，以及统一入口的参数示例。C 负责按文档验证可复现性。
