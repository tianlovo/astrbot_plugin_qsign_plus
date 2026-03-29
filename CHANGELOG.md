# Changelog

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
