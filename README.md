# 影音 / 电子书搜索聚合（Vue + Flask）

顶部可切换「音乐 / 视频 / 电子书」三种模式。

**音乐**——聚合三个网站的搜索、在线播放、下载：

- [歌曲海 gequhai.com](https://www.gequhai.com/)
- [音乐坊 yyfang.top](https://yyfang.top/)
- [放屁网 fangpi.net](https://www.fangpi.net/)

**视频**——多源聚合影视综合搜索 + 选集在线播放（m3u8 直链用 hls.js 播放）：

- [zg01.inavs.cn](https://zg01.inavs.cn/)：HTML 站，可搜索、拿剧集、在线播放
- 免费采集 API（MacCMS JSON，`?ac=detail&wd=` 直接返回含 m3u8 的播放地址）：非凡 / 暴风 / 如意 / 天堂 / 极速 / 最大 等
- novipnoad.ca / dushe3.app / ymck.pro：有人机验证（Cloudflare / cdndefend），服务器端无法抓取，仅提供「打开原站」入口

> 多个来源会并发搜索、结果交错合并，每个海报右上角标注来源。不同源走不同 CDN，某条线路被拦时可换其它来源/线路。**不做任何 VIP 破解**——只聚合本身免费、可直链的资源；腾讯/优酷/爱奇艺等会员外链只提供「打开原站」按钮。

**电子书**——多源聚合电子书搜索 + 站内在线阅读（支持小屏）/ 下载：

- 服务端聚合抓取：
  - [安娜的档案 zh.annas-archive.gl](https://zh.annas-archive.gl/)：海量电子书检索（PDF/EPUB/MOBI…），下载跳转原站下载页
  - [小璃盘 xiaolipan.com](https://www.xiaolipan.com/)：kindle/PDF/txt/mobi/epub 电子书下载
  - [笔趣阁 xiunews.com](http://www.xiunews.com/)：小说站，支持**站内在线阅读**与**下载整本 TXT**
- 仅提供「打开原站搜索」入口（Cloudflare 人机验证 / 动态反爬，服务器端无法抓取）：[读书派](https://www.dushupai.com/) / [飞库](https://feiku6.com/) / [Lunarora](https://lunarora.com/) / [识典古籍](https://www.shidianguji.com/)

> 与视频同理，笔趣阁等小说站的部分页面会拦截机房/服务器 IP（连接超时）。在本机（住宅宽带）运行时通常能正常在线阅读 / 下载；在服务器上若超时，可改用「打开原站」入口。在线阅读器内置目录、上/下一章、字号 +/-，并针对小屏做了全屏自适应。

## 启动

```bash
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

电子书模式：

- 输入书名 / 作者综合搜索，结果以封面网格展示（封面 / 书名 / 作者或格式大小 / 来源），右上角标注格式（PDF/EPUB…）或「可阅读」
- 点击书籍查看详情：封面、作者、简介、下载入口 / 原站页面
- 笔趣阁来源额外提供「开始在线阅读」（站内阅读器）与「下载 TXT」（后端并发抓取各章拼成整本 TXT）
- 安娜的档案 / 小璃盘提供下载入口（跳转原站下载页）
- 阅读器支持小屏：全屏阅读、目录、上/下一章、字号 +/-

> 注意：部分 m3u8 CDN 会拦截机房 / 服务器 IP（返回 403）。在本机（住宅宽带）运行时通常能正常播放；若提示「该源拦截了服务器 IP」，可换条线路或点「原站页面」。

## 项目结构

```
.
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

视频：

- zg01（HTML 站）：搜索 `/vodsearch/关键词-------------.html`；详情 `/voddetail/{id}.html` 抓剧集；播放页 `/vodplay/{id}-{线路}-{集}.html` 从内嵌 `player_aaaa` JSON 取 `url`
- 免费采集 API（MacCMS JSON）：搜索 `{api}?ac=detail&wd=关键词`；详情 `{api}?ac=detail&ids={id}`，从 `vod_play_from`（线路，`$$$` 分隔）+ `vod_play_url`（每集 `名称$地址`，`#` 分隔）解析出每条线路的剧集与 m3u8 直链
- 后端接口：`/api/video/search`（并发聚合所有来源）、`/api/video/detail`、`/api/video/parse`（仅 zg01 需要）、`/api/video/stream`（m3u8 代理 + 分片改写）

电子书：

- 安娜的档案：搜索 `/search?q=关键词`，结果块取 `/md5/{hash}` 与格式/大小/语言；详情 `/md5/{hash}` 取书名/简介/封面，下载跳转该页
- 小璃盘：搜索 `/search.html?keyword=关键词`，结果取 `/p/{id}.html`；详情页内嵌 `downPayParams` 含 `download_url`
- 笔趣阁（GBK 编码）：搜索 `/modules/article/search.php?searchkey=关键词(gbk)`；详情 `/{书号}/` 抓目录；章节页 `/{书号}/{章号}.html` 取 `#content` 正文
- 后端接口：`/api/book/search`（并发聚合可抓取来源、交错合并、返回 blocked 原站入口）、`/api/book/detail`、`/api/book/chapter`（笔趣阁站内阅读）、`/api/book/download`（笔趣阁整本 TXT）

## 常见问题

**浏览器打不开 http://127.0.0.1:5178 / 无法访问此网站**

1. 先确认终端里出现了 `Running on http://127.0.0.1:5178`，服务必须保持运行（不要关闭该终端窗口）。
2. 若 `./start.sh` 报 `Permission denied`，执行 `chmod +x start.sh` 后重试，或使用 `bash start.sh`。
3. 确认本机已安装 Python 3（`python3 --version`），首次启动需联网安装依赖。
4. 必须访问 `http://127.0.0.1:5178`（注意是 `http` 而非 `https`），不要使用其它端口。

请支持正版音乐，本工具仅供学习交流。
