# 音乐搜索聚合（Vue + Flask）

聚合三个网站的搜索、在线播放、下载功能：

- [歌曲海 gequhai.com](https://www.gequhai.com/)
- [音乐坊 yyfang.top](https://yyfang.top/)
- [放屁网 fangpi.net](https://www.fangpi.net/)

## 启动

```bash
cd ~/Desktop/music-search-vue
./start.sh
```

浏览器打开：**http://127.0.0.1:5178**

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

请支持正版音乐，本工具仅供学习交流。
