# 音乐搜索聚合（Vue + Flask）

聚合三个网站的搜索、在线播放、下载功能：

- [歌曲海 gequhai.com](https://www.gequhai.com/)
- [音乐坊 yyfang.top](https://yyfang.top/)
- [放屁网 fangpi.net](https://www.fangpi.net/)

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

- 搜索歌曲（支持全部 / 单站来源）
- 点击结果在线播放
- 下载 MP3（或通过网盘链接）
- 放屁网若被 Cloudflare 拦截，会自动显示嵌入搜索页

## 项目结构

```
music-search-vue/
├── index.html    # Vue 3 前端
├── server.py     # Flask 后端代理（解决跨域 + 解析页面）
├── start.sh      # 一键启动
└── requirements.txt
```

## 说明

- 歌曲海：搜索 `/s/关键词`，播放链接通过 `/api/music` 获取
- 音乐坊：搜索 `/search`，详情页内嵌 JSON 含 `music_mp3Url`
- 放屁网：服务器端可能被 403，此时使用 iframe 嵌入原站搜索

## 常见问题

**浏览器打不开 http://127.0.0.1:5178 / 无法访问此网站**

1. 先确认终端里出现了 `Running on http://127.0.0.1:5178`，服务必须保持运行（不要关闭该终端窗口）。
2. 若 `./start.sh` 报 `Permission denied`，执行 `chmod +x start.sh` 后重试，或使用 `bash start.sh`。
3. 确认本机已安装 Python 3（`python3 --version`），首次启动需联网安装依赖。
4. 必须访问 `http://127.0.0.1:5178`（注意是 `http` 而非 `https`），不要使用其它端口。

请支持正版音乐，本工具仅供学习交流。
