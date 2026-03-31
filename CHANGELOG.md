# Changelog

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
