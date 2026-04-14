# astrbot_plugin-pixiv

一个偏自用的 AstrBot 插件。

作用很简单：从自己的 Pixiv 收藏图库静态数据里随机抽一张图，直接发到当前会话。

目前支持两种触发方式：

- 指令：`/收藏`、`/pixiv`、`/来张图`
- 函数工具：`pixiv_random_image`

## 说明

这个插件默认是按我自己的图库结构写的，数据源来自：

- `images.json`
- `image/preview`
- `image/original`

也就是典型的 `PixivCollection` 静态部署方式。

第一版只做了最常用的能力：

- 随机抽图
- 安全模式过滤
- 最近若干次去重
- 远程图片发送失败后自动下载回退
- 管理员手动刷新缓存

没有做标签搜索、画师搜索、数据库联动之类的扩展功能。

## 配置

至少需要填写这两个：

- `data_url`：`images.json` 的完整地址
- `preview_base_url`：预览图目录地址

可选项：

- `original_base_url`
- `site_url`
- `safe_mode`
- `max_sanity_level`
- `send_mode`
- `refresh_interval_minutes`
- `recent_avoid_count`
- `allowed_groups`
- `admin_can_disable_safe_mode`

## 使用

普通用法：

```text
/收藏
/pixiv
/来张图
```

管理员命令：

```text
/收藏刷新
/收藏状态
/收藏安全 开
/收藏安全 关
```

## 安装

把插件目录放到 AstrBot 的 `data/plugins` 下即可。

如果是我这边当前的目录结构，就是类似：

```text
AstrBot/data/plugins/astrbot_plugin_pixiv_random
```

然后在 AstrBot 插件配置里填好图库地址。

## 备注

这个插件更适合“自己有一套现成图库”的场景。

如果后面要做按标签搜图、按作者搜图、随机规则更复杂，比较适合再单独加 API 层。
