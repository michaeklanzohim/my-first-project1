# 影音搜索聚合（Vue + Flask）

顶部可切换「音乐 / 视频」两种模式。

**音乐**——聚合三个网站的搜索、在线播放、下载：

- [歌曲海 gequhai.com](https://www.gequhai.com/)
- [音乐坊 yyfang.top](https://yyfang.top/)
- [放屁网 fangpi.net](https://www.fangpi.net/)

**视频**——zg01 影视综合搜索 + 选集在线播放（m3u8 直链用 hls.js 播放）：

- [zg01.inavs.cn](https://zg01.inavs.cn/)：可搜索、拿剧集、在线播放
- novipnoad.ca / dushe3.app / ymck.pro：有人机验证（Cloudflare / cdndefend），服务器端无法抓取，仅提供「打开原站」入口

## 启动

```bash
cd music-search-vue
./start.sh
```

首次运行会自动创建虚拟环境并安装依赖，稍等片刻后终端会显示：

```
 * Running on http://127.0.0.1:5178
```

看到上面这行后，再用浏览器打开：**http://127.0.0.1:5178**

> 如果运行 `./start.sh` 报 `Permission denied`，说明脚本没有可执行权限，先执行一次
> `chmod +x start.sh`，或直接用 `bash start.sh` 启动。

## 功能

音乐模式：

- 搜索歌曲（支持全部 / 单站来源）
- 点击结果在线播放
- 下载 MP3（或通过网盘链接）
- 放屁网若被 Cloudflare 拦截，会自动显示嵌入搜索页

视频模式：

- 输入影视名综合搜索，结果以海报网格展示（封面 / 片名 / 类型年份 / 更新状态）
- 点击影视加载剧集，按线路分组，自动选中可直链播放的线路与第 1 集
- m3u8 直链线路用 hls.js 在线播放（后端 `/api/video/stream` 代理并改写播放列表，绕过跨域 / referer 限制）
- 优酷等外链线路提供「打开原站播放」按钮

> 注意：部分 m3u8 CDN 会拦截机房 / 服务器 IP（返回 403）。在本机（住宅宽带）运行时通常能正常播放；若提示「该源拦截了服务器 IP」，可换条线路或点「原站页面」。

## 项目结构

```
music-search-vue/
├── index.html    # Vue 3 前端（音乐 / 视频两种模式）
├── server.py     # Flask 后端代理（解决跨域 + 解析页面）
├── start.sh      # 一键启动
└── requirements.txt
```

## 说明

音乐：

- 歌曲海：搜索 `/s/关键词`，播放链接通过 `/api/music` 获取
- 音乐坊：搜索 `/search`，详情页内嵌 JSON 含 `music_mp3Url`
- 放屁网：服务器端可能被 403，此时使用 iframe 嵌入原站搜索

视频（zg01，MacCMS 站）：

- 搜索：`/vodsearch/关键词-------------.html`
- 详情：`/voddetail/{id}.html`，抓取剧集列表并按线路分组
- 播放页：`/vodplay/{id}-{线路}-{集}.html`，从内嵌 `player_aaaa` JSON 取 `url`
- 后端接口：`/api/video/search`、`/api/video/detail`、`/api/video/parse`、`/api/video/stream`（m3u8 代理 + 分片改写）

## 常见问题

**浏览器打不开 http://127.0.0.1:5178 / 无法访问此网站**

1. 先确认终端里出现了 `Running on http://127.0.0.1:5178`，服务必须保持运行（不要关闭该终端窗口）。
2. 若 `./start.sh` 报 `Permission denied`，执行 `chmod +x start.sh` 后重试，或使用 `bash start.sh`。
3. 确认本机已安装 Python 3（`python3 --version`），首次启动需联网安装依赖。
4. 必须访问 `http://127.0.0.1:5178`（注意是 `http` 而非 `https`），不要使用其它端口。

请支持正版音乐，本工具仅供学习交流。
