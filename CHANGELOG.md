# Changelog

## v2.8.2 (2026-04-01)

### 优化
- **At机器人奖励日志**: 添加 info 级别日志输出
  - 记录用户 at 机器人时的概率判定信息（随机值/目标概率）
  - 记录是否触发奖励的信息
  - 记录触发奖励时获得的金币数量
  - 记录用户达到每日上限的信息
  - 记录奖励发放后的统计信息（今日次数/上限，累计金币）

## v2.8.1 (2026-04-01)

### 修复
- **At机器人奖励功能**: 修复 DataManager 缺少 at 奖励方法的问题
  - 在 `DataManager` 类中添加 `record_at_reward()` 方法
  - 在 `DataManager` 类中添加 `get_user_at_reward_count()` 方法
  - 在 `DataManager` 类中添加 `get_user_at_reward_total()` 方法
  - 这些方法代理到 `QsignDatabase` 类的对应方法

## v2.8.0 (2026-04-01)

### 新增
- **At机器人随机金币奖励**: 用户at机器人有概率获得随机金币奖励
  - 新增 `at_reward` 配置组，包含以下配置项：
    - `enable_at_reward`: 是否启用at奖励功能（默认true）
    - `at_reward_probability`: 获得奖励的概率，0-1之间（默认0.3）
    - `at_reward_min`: 最小奖励金额（默认1.0）
    - `at_reward_max`: 最大奖励金额（默认10.0）
    - `at_reward_daily_limit`: 每日奖励上限次数（默认5）
    - `at_reward_timezone`: 时区设置（默认Asia/Shanghai）
  - 新增 `on_at_bot()` 方法监听群消息中的at事件
  - 使用 `event.is_at_or_wake_command` 检测at机器人
  - 概率判定成功后发放随机范围内的金币奖励
  - 每个成员每日有奖励次数上限，达到上限后静默处理
  - 奖励消息包含获得金额、今日次数和累计获得金额
  - 新增 `at_reward_records` 数据库表记录奖励数据
  - 新增 `record_at_reward()`、`get_user_at_reward_count()`、`get_user_at_reward_total()` 数据库方法

## v2.7.4 (2026-04-01)

### 移除
- **打卡奖励服务**: 移除 `checkin_reward_service` 服务及相关功能
  - 删除 `services/checkin_reward_service.py` 文件
  - 从 `_conf_schema.json` 中移除打卡奖励配置（enable_checkin_reward、poll_interval、base_reward 等）
  - 从 `core/database.py` 中移除打卡记录表及相关方法（record_checkin、get_checkin_records、get_user_checkin_record）

## v2.7.3 (2026-04-01)

### 优化
- **指令格式优化**: 存款和取款指令现在可以不带空格
  - 支持格式：`存款1234`、`存钱 1234`、`存款 1234`
  - 支持格式：`取款1234`、`取钱 1234`、`取款 1234`
  - 正则表达式从 `\s+` 改为 `\s*`，空格变为可选

## v2.7.2 (2026-04-01)

### 优化
- **文字版本信息完善**: 关闭图片卡片时，文字版本现在包含与图片卡片完全相同的信息
  - **签到文字版**: 包含用户名称、财富等级、状态、签到时间、连续签到天数
    - 今日收益明细：基础收益、雇员加成、连续签到加成、银行利息、受雇惩罚（如有）、总收益
    - 资产状况：现金、银行存款、总资产
    - 雇员数量
  - **查询文字版**: 包含用户ID、财富等级、状态、查询时间
    - 资产状况：现金、银行存款、总资产、连续签到天数
    - 明日预计收入：基础收益、雇员加成、连续签到加成、银行利息、总收入
    - 雇员列表（带人数）和雇主信息

## v2.7.1 (2026-04-01)

### 新增
- **图片卡片开关配置**: 在基础配置中新增 `enable_image_card` 选项
  - 默认开启（true），保持原有图片卡片功能
  - 关闭后（false），签到和查询将只发送文字信息，不生成图片卡片
  - 文字信息包含完整的资产数据（现金、银行存款、总资产、雇员、雇主、连续签到等）

### 修复
- **消息撤回功能**: 修复消息撤回失败的问题
  - 修改 `send_text_reply` 使用 `send_group_msg` API 发送消息，正确获取 message_id
  - 修改 `recall_message` 使用 `client.delete_msg` 方式撤回消息

## v2.7.0 (2026-04-01)

### 新增
- **优化信息查询流程**: "我的信息"查询现在先发送文字版本，再异步生成图片
  - 新增 `_query_states` 字典管理查询状态，处理多群多用户同时查询的竞态问题
  - 先发送用户资产信息的文字版本（现金、银行存款、总资产、雇佣关系等）
  - 文字消息附带"正在生成图片卡片，请稍候..."提示
  - 图片生成完成后自动撤回文字消息并发送图片
  - 图片生成失败时保留文字消息，用户仍能看到资产信息
  - 同一用户在图片生成期间重复查询会被忽略并提示

- **消息撤回功能**: 在 `message_utils.py` 中新增 `recall_message()` 函数
  - 支持撤回指定消息ID的消息
  - 目前支持 aiocqhttp 平台

### 重构
- **sign_query 方法**: 完全重构查询流程
  - 先获取用户数据并格式化为文字版本
  - 使用状态管理防止重复查询
  - 异步生成图片，完成后替换文字消息

## v2.6.0 (2026-04-01)

### 新增
- **消息引用回复**: 所有机器人回复现在都会引用用户触发指令的原始消息
  - 新增 `utils/message_utils.py` 模块，封装消息回复功能
  - 新增 `send_text_reply()` 函数，发送带引用的纯文本消息
  - 新增 `send_image_reply()` 函数，发送带引用的图片消息
  - 新增 `create_reply_chain()` 辅助函数，创建带引用的消息链

### 重构
- **主程序更新**: 更新 `main.py` 中所有指令处理方法的回复方式
  - 购买指令：所有回复都使用带引用的消息发送
  - 出售指令：所有回复都使用带引用的消息发送
  - 签到指令：所有回复（包括图片和纯文本）都使用带引用的消息发送
  - 排行榜指令：所有回复都使用带引用的消息发送
  - 赎身指令：所有回复都使用带引用的消息发送
  - 查询指令：所有回复（包括图片和纯文本）都使用带引用的消息发送
  - 存款指令：所有回复都使用带引用的消息发送
  - 取款指令：所有回复都使用带引用的消息发送

## v2.5.1 (2026-03-31)

### 修复
- **购买/出售机器人**: 修复at机器人时被忽略的问题
  - 新增 `get_first_at_user()` 函数获取消息中第一个at（包括机器人）
  - 修改购买逻辑：当没有找到非机器人的at时，尝试获取第一个at
  - 修改出售逻辑：同上

- **存款/取款指令**: 修复参数不匹配问题
  - AstrBot的 `filter.regex` 装饰器不支持自动传递捕获组参数
  - 修改 `deposit()` 和 `withdraw()` 方法，从 `event.message_str` 手动解析金额
  - 移除 `amount_str` 参数，改为在方法内部解析

## v2.5.0 (2026-03-29)

### 重构
- **完全移除YAML存储**: 所有数据统一使用SQLite数据库存储
  - 新增 `purchase_counts` 表存储用户被购买次数
  - 数据库新增 `get_purchase_count` 和 `increment_purchase_count` 方法
  - 更新 `migrate_from_yaml` 方法支持迁移购买次数数据
  - 数据管理器完全移除YAML文件操作

- **数据管理器简化**: 重构 `core/data_manager.py`
  - 移除 `aiofiles` 和 `yaml` 导入
  - 移除YAML文件路径和初始化代码
  - 移除 `_load_yaml_async` 和 `_save_yaml_async` 方法
  - 移除 `save_purchase_data` 方法
  - `get_purchase_count` 和 `increment_purchase_count` 改为异步方法
  - 简化 `init` 方法，统一处理YAML数据迁移

- **财富系统更新**: 更新 `core/wealth_system.py`
  - `get_purchase_count` 调用添加 `await`

- **主程序更新**: 更新 `main.py`
  - `increment_purchase_count` 调用添加 `await`
  - 移除 `save_purchase_data` 调用

### 其他
- **依赖更新**: 从 `requirements.txt` 移除 `pyyaml` 和 `requests`
- **文档更新**: 更新 `README.md`，添加数据存储说明和详细配置项说明
- **配置结构**: 配置文件使用父子级分类结构（basic/trade/contract/admin）

## v2.4.0 (2026-03-29)

### 重构
- **数据库迁移**: 从YAML迁移到SQLite数据库
  - 新增 `core/database.py` 模块，提供完整的数据库操作支持
  - 用户财富数据、雇员关系现在存储在SQLite数据库中
  - 支持群组隔离，所有数据按 group_id 隔离存储
  - 保留YAML用于购买次数配置存储
  - 初始化时自动检查并迁移旧YAML数据到数据库

- **数据管理器重构**: 重构 `core/data_manager.py`
  - 导入并使用 QsignDatabase 进行数据持久化
  - `get_user_data` 方法改为异步，从数据库查询用户数据
  - `save_user_data` 方法改为异步，保存数据到数据库
  - 新增 `add_contractor`, `remove_contractor`, `clear_contractors` 方法操作雇员关系
  - 新增 `get_leaderboard` 方法从数据库获取排行榜
  - 新增 `close` 方法关闭数据库连接

- **财富系统重构**: 重构 `core/wealth_system.py`
  - 所有方法改为异步支持
  - `calculate_dynamic_wealth_value` 方法现在接收 group_id 参数
  - `get_total_contractor_rate` 方法现在接收 group_id 参数
  - `calculate_sign_income` 和 `calculate_tomorrow_income` 方法现在接收 group_id 参数
  - 所有数据库操作都使用 group_id 进行群组隔离

- **主程序重构**: 重构 `main.py`
  - 更新所有指令处理方法，使用 `await` 调用异步方法
  - 购买/出售逻辑使用新的数据库操作方法
  - 签到逻辑使用新的数据库操作方法
  - 排行榜查询使用数据库方法 `get_leaderboard`
  - `terminate` 方法现在关闭数据库连接
  - 所有用户数据操作都使用 group_id

- **卡片渲染器更新**: 更新 `services/card_renderer.py`
  - 适配异步数据管理器调用
  - `prepare_render_data` 方法现在使用 `await` 获取用户数据

### 新增
- **依赖项**: 添加 `aiosqlite` 依赖用于异步SQLite操作

## v2.3.0 (2026-03-29)

### 新增
- **管理员保护机制**: 新增群主/管理员购买保护
  - 新增 `_is_user_admin()` 方法检查用户是否为群主或管理员
  - 购买指令现在会检查目标用户身份，群主/管理员不可被购买
  - 新增 `admin_price_bonus` 配置项，默认 0.5（身价加成50%）

- **卡片渲染服务**: 新增 `services/card_renderer.py` 模块
  - 封装卡片渲染逻辑，支持签到卡片和信息查询卡片
  - 使用 `ImageCacheService` 获取头像和背景图
  - 支持 HTML 模板加载和渲染数据准备

### 重构
- **主程序架构优化**: 重构 `main.py` 文件
  - 导入所有新模块（utils.helpers, core.data_manager, core.wealth_system, services.image_cache, services.card_renderer）
  - 在 `__init__` 中初始化各服务（DataManager, WealthSystem, ImageCacheService, CardRenderer）
  - 简化所有指令处理方法，调用模块接口
  - 保留事件处理和流程编排逻辑

## v2.2.0 (2026-03-29)

### 修改
- **指令触发方式**: 所有命令现在需要at机器人才能触发
  - 新增 `_is_at_bot()` 方法检查消息是否at了机器人
  - 所有指令处理方法（购买、出售、签到、排行榜、赎身、查询、存款、取款）都添加了at检查

### 新增
- **图片下载重试机制**: 为 `_image_to_base64()` 方法添加重试功能
  - 默认重试3次
  - 使用指数退避策略（0.5s, 1s, 1.5s）
  - 提高图片下载成功率

## v2.1.0 (2026-03-29)

### 修改
- **作者信息**: 更新作者为 tianluoqaq
- **仓库地址**: 更新仓库地址为 https://github.com/tianlovo/astrbot_plugin_qsign_plus

### 新增
- **QQ群白名单配置**: 新增 `enabled_groups` 配置项
  - 只有在白名单列表中的群才能触发插件功能
  - 配置为空列表时允许所有群使用

### 修复
- **购买/出售at目标**: 修复at目标用户识别问题
  - 当用户at机器人后再at其他用户时，现在能正确识别被at的目标用户
  - 排除机器人自身的at，避免误操作
