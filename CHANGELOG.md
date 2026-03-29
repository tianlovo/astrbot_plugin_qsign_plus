# Changelog

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
