#!/usr/bin/env python3
"""Music search/play/download proxy for gequhai, yyfang, fangpi."""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import socket
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Any, Iterator

from bs4 import BeautifulSoup
from flask import Flask, Response, has_request_context, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    from curl_cffi import requests as cffi_requests

    HAS_CFFI = True
except ImportError:
    HAS_CFFI = False

APP_DIR = __file__.rsplit("/", 1)[0] if "/" in __file__ else "."
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

app = Flask(__name__, static_folder=APP_DIR, static_url_path="")
CORS(app)


@dataclass
class SongItem:
    source: str
    id: str
    name: str
    artist: str
    cover: str = ""
    url: str = ""


def http_get(url: str, headers: dict | None = None, referer: str | None = None) -> str:
    hdrs = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    if HAS_CFFI:
        try:
            resp = cffi_requests.get(url, headers=hdrs, impersonate="chrome124", timeout=25)
            resp.raise_for_status()
            return resp.text
        except Exception:
            pass
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "ignore")


def http_post_form(url: str, data: dict, headers: dict | None = None) -> dict:
    hdrs = {
        "User-Agent": UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Requested-With": "XMLHttpRequest",
        "X-Custom-Header": "SecretKey",
    }
    if headers:
        hdrs.update(headers)
    body = urllib.parse.urlencode(data).encode()
    if HAS_CFFI:
        resp = cffi_requests.post(url, data=body, headers=hdrs, impersonate="chrome124", timeout=25)
        resp.raise_for_status()
        return resp.json()
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def is_direct_audio(url: str) -> bool:
    if not url.startswith("http"):
        return False
    lower = url.lower()
    if "pan." in lower or "quark.cn" in lower:
        return False
    return any(
        token in lower
        for token in (".mp3", ".flac", ".m4a", ".wav", ".ogg", "kuwo.cn", "sycdn.kuwo")
    )


def proxy_request_headers(url: str, referer: str = "", range_hdr: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if "kuwo.cn" in url:
        headers["Referer"] = "https://www.kuwo.cn/"
    elif referer:
        headers["Referer"] = referer
    if range_hdr:
        headers["Range"] = range_hdr
    elif has_request_context() and request.headers.get("Range"):
        headers["Range"] = request.headers.get("Range")
    return headers


def convert_kuwo_url(url: str) -> str:
    if "antiserver.kuwo.cn" not in url:
        return url
    try:
        parsed = urllib.parse.urlparse(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        query["type"] = "convert_url3"
        convert_url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
        if HAS_CFFI:
            resp = cffi_requests.get(
                convert_url,
                headers={"User-Agent": UA, "Referer": "https://www.kuwo.cn/"},
                impersonate="chrome124",
                timeout=20,
            )
            data = resp.json()
            if data.get("code") == 200 and data.get("url"):
                return data["url"]
    except Exception:
        pass
    return url


def stream_remote(url: str, referer: str = "") -> Response:
    url = convert_kuwo_url(url)
    headers = proxy_request_headers(url, referer)

    if HAS_CFFI:
        resp = cffi_requests.get(
            url,
            headers=headers,
            impersonate="chrome124",
            timeout=60,
            stream=True,
        )
        if resp.status_code not in (200, 206):
            return jsonify({"error": f"上游返回 {resp.status_code}"}), resp.status_code

        passthrough = {}
        for key in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
            if key in resp.headers:
                passthrough[key] = resp.headers[key]
        if "Content-Type" not in passthrough:
            passthrough["Content-Type"] = "audio/mpeg"

        def generate() -> Iterator[bytes]:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    yield chunk

        return Response(generate(), status=resp.status_code, headers=passthrough)

    req = urllib.request.Request(url, headers=headers)
    remote = urllib.request.urlopen(req, timeout=60)
    status = remote.status if hasattr(remote, "status") else 200
    out_headers = {
        "Content-Type": remote.headers.get("Content-Type", "audio/mpeg"),
        "Accept-Ranges": "bytes",
    }
    if remote.headers.get("Content-Length"):
        out_headers["Content-Length"] = remote.headers.get("Content-Length")
    if remote.headers.get("Content-Range"):
        out_headers["Content-Range"] = remote.headers.get("Content-Range")

    def generate() -> Iterator[bytes]:
        while True:
            chunk = remote.read(65536)
            if not chunk:
                break
            yield chunk

    return Response(generate(), status=status, headers=out_headers)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", name).strip()
    return cleaned or "music"


def verify_audio_url(url: str) -> bool:
    if not is_direct_audio(url):
        return False
    try:
        headers = proxy_request_headers(url, range_hdr="bytes=0-1023")
        if HAS_CFFI:
            resp = cffi_requests.get(
                url, headers=headers, impersonate="chrome124", timeout=15, stream=True
            )
            if resp.status_code not in (200, 206):
                resp.close()
                return False
            chunk = next(resp.iter_content(chunk_size=512), b"")
            resp.close()
            return len(chunk) > 0
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as remote:
            return remote.read(1) != b""
    except Exception:
        return False


def fallback_play_url(name: str, artist: str) -> str:
    if not name:
        return ""
    try:
        for item in search_gequhai(name):
            if artist and artist not in item.artist and item.name != name:
                continue
            detail = detail_gequhai(item.id)
            url = detail.get("playUrl", "")
            if url and verify_audio_url(url):
                return url
    except Exception:
        pass
    return ""


def decode_gequ_extra(encoded: str) -> str:
    if not encoded:
        return ""
    try:
        fixed = encoded.replace("#", "H").replace("%", "S")
        pad = "=" * ((4 - len(fixed) % 4) % 4)
        return base64.b64decode(fixed + pad).decode("utf-8", "ignore")
    except Exception:
        return ""


def fetch_gequhai_cover(song_id: str) -> str:
    """Lightweight cover lookup: fetch the play page and read window.mp3_cover."""
    try:
        html = http_get(
            f"https://www.gequhai.com/play/{song_id}",
            referer="https://www.gequhai.com/",
        )
        m = re.search(r"window\.mp3_cover\s*=\s*'([^']*)'", html)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


def enrich_gequhai_covers(items: list[SongItem]) -> None:
    """Fill cover URLs for gequhai results concurrently (covers are not in the list HTML)."""
    pending = [it for it in items if not it.cover]
    if not pending:
        return
    with ThreadPoolExecutor(max_workers=min(10, len(pending))) as pool:
        futures = {pool.submit(fetch_gequhai_cover, it.id): it for it in pending}
        for future in as_completed(futures):
            item = futures[future]
            try:
                item.cover = future.result() or item.cover
            except Exception:
                pass


def search_gequhai(keyword: str) -> list[SongItem]:
    url = f"https://www.gequhai.com/s/{urllib.parse.quote(keyword)}"
    html = http_get(url, referer="https://www.gequhai.com/")
    items: list[SongItem] = []
    for row in re.findall(r"<tr>[\s\S]*?</tr>", html):
        m_id = re.search(r'href="/play/(\d+)"', row)
        m_name = re.search(r"font-weight-bold\">\s*([^<]+)", row)
        m_artist = re.search(r'<td style="color: #666[^"]*">([^<]+)</td>', row)
        if not m_id or not m_name:
            continue
        sid = m_id.group(1)
        items.append(
            SongItem(
                source="gequhai",
                id=sid,
                name=m_name.group(1).strip(),
                artist=(m_artist.group(1).strip() if m_artist else ""),
                url=f"https://www.gequhai.com/play/{sid}",
            )
        )
    items = items[:30]
    enrich_gequhai_covers(items)
    return items


def build_qualities(
    *,
    direct_mp3: str = "",
    flac_pan: str = "",
    mp3_pan: str = "",
    generic_pan: str = "",
) -> list[dict[str, Any]]:
    """Assemble quality/download tiers, highest quality first.

    kind='direct' -> in-app proxy download; kind='pan' -> open netdisk share link.
    True lossless is only distributed via netdisk (kuwo gates FLAC behind VIP).
    """
    tiers: list[dict[str, Any]] = []
    if flac_pan:
        tiers.append({"label": "无损 FLAC（网盘）", "format": "flac", "lossless": True, "kind": "pan", "url": flac_pan})
    if direct_mp3 and is_direct_audio(direct_mp3):
        tiers.append({"label": "标准 MP3（直链下载）", "format": "mp3", "lossless": False, "kind": "direct", "url": direct_mp3})
    if mp3_pan:
        tiers.append({"label": "标准 MP3（网盘）", "format": "mp3", "lossless": False, "kind": "pan", "url": mp3_pan})
    if generic_pan and not flac_pan and not mp3_pan:
        tiers.append({"label": "原盘下载（网盘）", "format": "", "lossless": False, "kind": "pan", "url": generic_pan})
    return tiers


def detail_gequhai(song_id: str) -> dict[str, Any]:
    page_url = f"https://www.gequhai.com/play/{song_id}"
    html = http_get(page_url, referer="https://www.gequhai.com/")
    def pick(var: str) -> str:
        m = re.search(rf"window\.{var}\s*=\s*'([^']*)'", html)
        if m:
            return m.group(1)
        m = re.search(rf'window\.{var}\s*=\s*([^;]+);', html)
        return (m.group(1).strip().strip("'") if m else "")

    play_id = pick("play_id")
    title = pick("mp3_title")
    artist = pick("mp3_author")
    cover = pick("mp3_cover")
    extra = pick("mp3_extra_url")
    pan_url = decode_gequ_extra(extra)

    play_url = ""
    if play_id:
        try:
            result = http_post_form(
                "https://www.gequhai.com/api/music",
                {"id": play_id, "type": 0},
                headers={"Referer": page_url},
            )
            if result.get("code") == 200:
                play_url = result.get("data", {}).get("url", "")
                play_url = convert_kuwo_url(play_url)
        except Exception:
            pass

    return {
        "source": "gequhai",
        "id": song_id,
        "name": title,
        "artist": artist,
        "cover": cover,
        "playUrl": play_url,
        "downloadUrl": play_url or pan_url,
        "panUrl": pan_url,
        "qualities": build_qualities(direct_mp3=play_url, generic_pan=pan_url),
        "pageUrl": page_url,
    }


def search_yyfang(keyword: str) -> list[SongItem]:
    url = f"https://yyfang.top/search?page=0&keyword={urllib.parse.quote(keyword)}"
    html = http_get(url, referer="https://yyfang.top/")
    items: list[SongItem] = []
    for m in re.finditer(
        r'href="/music/info\.html\?id=([^"]+)"[\s\S]*?'
        r'<img[^>]+src="([^"]+)"[\s\S]*?'
        r'<div class="song_info">\s*<div>([^<]+)</div>\s*<div>([^<]+)</div>',
        html,
    ):
        items.append(
            SongItem(
                source="yyfang",
                id=m.group(1),
                name=m.group(3).strip(),
                artist=m.group(4).strip(),
                cover=m.group(2).strip(),
                url=f"https://yyfang.top/music/info.html?id={m.group(1)}",
            )
        )
    return items[:30]


def parse_yyfang_detail_json(html: str) -> dict[str, Any]:
    m = re.search(r"detail\s*=\s*JSON\.parse\('(.+?)'\)", html, re.S)
    if not m:
        return {}
    raw = m.group(1).replace("\\/", "/")
    return json.loads(raw)


def detail_yyfang(song_id: str) -> dict[str, Any]:
    page_url = f"https://yyfang.top/music/info.html?id={urllib.parse.quote(song_id)}"
    html = http_get(page_url, referer="https://yyfang.top/")
    data = parse_yyfang_detail_json(html)
    play_url = data.get("music_mp3Url") or data.get("music_mp3url") or ""
    flac_url = data.get("music_flacUrl") or data.get("music_flacurl") or ""
    mp3_pan = data.get("mp3_url") or ""
    flac_pan = data.get("flac_url") or ""
    if play_url:
        play_url = convert_kuwo_url(play_url)
    if flac_url:
        flac_url = convert_kuwo_url(flac_url)
    if not play_url or not verify_audio_url(play_url):
        fallback = fallback_play_url(data.get("music_name", ""), data.get("music_artist", ""))
        if fallback:
            play_url = fallback
    return {
        "source": "yyfang",
        "id": song_id,
        "name": data.get("music_name", ""),
        "artist": data.get("music_artist", ""),
        "cover": data.get("music_cover", ""),
        "playUrl": play_url,
        "downloadUrl": play_url or flac_url or mp3_pan,
        "flacUrl": flac_url,
        "panUrl": mp3_pan,
        "qualities": build_qualities(direct_mp3=play_url, flac_pan=flac_pan, mp3_pan=mp3_pan),
        "pageUrl": page_url,
    }


def search_fangpi(keyword: str) -> tuple[list[SongItem], dict[str, Any]]:
    url = f"https://www.fangpi.net/s/{urllib.parse.quote(keyword)}"
    meta = {"available": False, "iframeUrl": url, "message": ""}
    try:
        html = http_get(url, referer="https://www.fangpi.net/")
        if "Just a moment" in html or "challenge-platform" in html:
            meta["message"] = "放屁网启用了 Cloudflare，当前服务器无法直接抓取，已提供站内嵌入搜索。"
            return [], meta
        soup = BeautifulSoup(html, "lxml")
        items: list[SongItem] = []
        card = next(
            (
                c
                for c in soup.select("div.card")
                if "搜索结果" in c.get_text(" ", strip=True)
            ),
            None,
        )
        if not card:
            meta["message"] = "未解析到搜索结果，可改用嵌入页面搜索。"
            return [], meta
        for row in card.select("div.row"):
            detail = row.select_one('a[href^="/music/"][title]')
            if not detail:
                continue
            href = detail.get("href", "")
            sid = href.rsplit("/", 1)[-1]
            name_el = row.select_one("span.text-primary") or detail
            artist_el = row.select_one("small.text-jade")
            items.append(
                SongItem(
                    source="fangpi",
                    id=sid,
                    name=name_el.get_text(strip=True),
                    artist=artist_el.get_text(strip=True) if artist_el else "",
                    url=f"https://www.fangpi.net{href}",
                )
            )
        meta["available"] = len(items) > 0
        return items[:30], meta
    except Exception as exc:
        meta["message"] = f"放屁网暂不可用：{exc}"
        return [], meta


def detail_fangpi(song_id: str) -> dict[str, Any]:
    page_url = f"https://www.fangpi.net/music/{song_id}"
    html = http_get(page_url, referer="https://www.fangpi.net/")
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", string=re.compile(r"window\.appData"))
    download_result: dict[str, Any] = {}
    if script and script.string:
        m = re.search(
            r"JSON\.parse\(\s*(?P<lit>([\"'])(?:\\.|(?!\2).)*?\2)\s*\)",
            script.string,
            re.S,
        )
        if m:
            download_result = json.loads(json.loads(m.group("lit")))
    play_id = download_result.get("play_id", "")
    play_url = ""
    if play_id and HAS_CFFI:
        try:
            resp = cffi_requests.post(
                "https://www.fangpi.net/api/play-url",
                json={"id": play_id},
                headers={"User-Agent": UA, "Referer": page_url, "Origin": "https://www.fangpi.net"},
                impersonate="chrome124",
                timeout=25,
            )
            payload = resp.json()
            play_url = payload.get("data", {}).get("url", "")
        except Exception:
            pass
    pan_url = ""
    for item in download_result.get("mp3_extra_urls", []) or []:
        link = item.get("share_link", "").replace("\\/", "/")
        if link:
            try:
                pan_url = base64.b64decode(link).decode("utf-8", "ignore")
                break
            except Exception:
                pass
    lyric_el = soup.find("div", id="content-lrc")
    return {
        "source": "fangpi",
        "id": song_id,
        "name": download_result.get("mp3_title", ""),
        "artist": download_result.get("mp3_author", ""),
        "cover": str(download_result.get("mp3_cover", "")).replace("\\/", "/"),
        "playUrl": play_url,
        "downloadUrl": play_url or pan_url,
        "panUrl": pan_url,
        "qualities": build_qualities(direct_mp3=play_url, generic_pan=pan_url),
        "lyric": lyric_el.get_text("\n", strip=True) if lyric_el else "",
        "pageUrl": page_url,
    }


@app.get("/")
def index():
    return send_from_directory(APP_DIR, "index.html")


@app.get("/api/search")
def api_search():
    q = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "all").lower()
    if not q:
        return jsonify({"error": "请输入搜索关键词"}), 400

    fangpi_meta: dict[str, Any] = {}

    tasks: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=3) as pool:
        if source in ("all", "gequhai"):
            tasks["gequhai"] = pool.submit(search_gequhai, q)
        if source in ("all", "yyfang"):
            tasks["yyfang"] = pool.submit(search_yyfang, q)
        if source in ("all", "fangpi"):
            tasks["fangpi"] = pool.submit(search_fangpi, q)

        gequhai_items: list[SongItem] = []
        yyfang_items: list[SongItem] = []
        fangpi_items: list[SongItem] = []
        if "gequhai" in tasks:
            try:
                gequhai_items = tasks["gequhai"].result()
            except Exception:
                gequhai_items = []
        if "yyfang" in tasks:
            try:
                yyfang_items = tasks["yyfang"].result()
            except Exception:
                yyfang_items = []
        if "fangpi" in tasks:
            try:
                fangpi_items, fangpi_meta = tasks["fangpi"].result()
            except Exception as exc:
                fangpi_items, fangpi_meta = [], {"available": False, "message": f"放屁网暂不可用：{exc}"}

    results: list[dict] = []
    results.extend(asdict(x) for x in gequhai_items)
    results.extend(asdict(x) for x in yyfang_items)
    results.extend(asdict(x) for x in fangpi_items)

    return jsonify({"query": q, "count": len(results), "items": results, "fangpi": fangpi_meta})


@app.get("/api/song")
def api_song():
    source = (request.args.get("source") or "").lower()
    song_id = (request.args.get("id") or "").strip()
    if not source or not song_id:
        return jsonify({"error": "缺少 source 或 id"}), 400
    try:
        if source == "gequhai":
            return jsonify(detail_gequhai(song_id))
        if source == "yyfang":
            return jsonify(detail_yyfang(song_id))
        if source == "fangpi":
            return jsonify(detail_fangpi(song_id))
        return jsonify({"error": f"未知来源: {source}"}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/stream")
def api_stream():
    target = request.args.get("url", "")
    referer = request.args.get("referer", "")
    if not is_direct_audio(target):
        return jsonify({"error": "无效或不支持的音频链接"}), 400
    try:
        return stream_remote(target, referer)
    except Exception as exc:
        return jsonify({"error": f"播放代理失败: {exc}"}), 502


@app.get("/api/download")
def api_download():
    target = request.args.get("url", "")
    referer = request.args.get("referer", "")
    filename = sanitize_filename(request.args.get("filename", "music.mp3"))
    if not filename.lower().endswith((".mp3", ".flac", ".m4a", ".wav", ".ogg")):
        filename += ".mp3"
    if not is_direct_audio(target):
        return jsonify({"error": "无效或不支持的下载链接"}), 400
    try:
        resp = stream_remote(target, referer)
        quoted = urllib.parse.quote(filename)
        resp.headers["Content-Disposition"] = f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}"
        return resp
    except Exception as exc:
        return jsonify({"error": f"下载失败: {exc}"}), 502


@app.get("/api/proxy")
def api_proxy():
    return api_stream()


# ===================== Video (zg01.inavs.cn, MacCMS) =====================

ZG01_BASE = "https://zg01.inavs.cn"

VIDEO_SITES = [
    {"key": "zg01", "name": "zg01.inavs.cn", "url": ZG01_BASE + "/", "scrapable": True},
    {
        "key": "novipnoad",
        "name": "novipnoad.ca",
        "url": "https://www.novipnoad.ca/",
        "scrapable": False,
        "note": "Cloudflare 人机验证，服务器端无法抓取，请打开原站搜索",
    },
    {
        "key": "dushe3",
        "name": "dushe3.app",
        "url": "https://www.dushe3.app/",
        "scrapable": False,
        "note": "cdndefend 人机验证，服务器端无法抓取，请打开原站搜索",
    },
    {
        "key": "ymck",
        "name": "ymck.pro",
        "url": "https://www.ymck.pro/",
        "scrapable": False,
        "note": "Cloudflare 人机验证，服务器端无法抓取，请打开原站搜索",
    },
]


@dataclass
class VideoItem:
    source: str
    id: str
    name: str
    cover: str = ""
    note: str = ""
    type: str = ""
    year: str = ""
    area: str = ""


def _node_text(node: Any) -> str:
    return node.get_text(strip=True) if node else ""


def _img_src(img: Any) -> str:
    if not img:
        return ""
    return (img.get("data-original") or img.get("data-src") or img.get("src") or "").strip()


def search_zg01(keyword: str) -> list[VideoItem]:
    url = f"{ZG01_BASE}/vodsearch/{urllib.parse.quote(keyword)}-------------.html"
    html = http_get(url, referer=ZG01_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    items: list[VideoItem] = []
    seen: set[str] = set()
    for card in soup.select(".module-card-item"):
        anchor = card.select_one('a.module-card-item-poster[href^="/voddetail/"]') or card.select_one(
            'a[href^="/voddetail/"]'
        )
        if not anchor:
            continue
        m = re.search(r"/voddetail/(\d+)\.html", anchor.get("href", ""))
        if not m:
            continue
        vid = m.group(1)
        if vid in seen:
            continue
        seen.add(vid)
        img = card.select_one("img")
        cover = _img_src(img)
        name = _node_text(card.select_one(".module-card-item-title")) or (img.get("alt") if img else "") or ""
        info = _node_text(card.select_one(".module-info-item-content"))
        year_match = re.search(r"(?:19|20)\d{2}", info)
        items.append(
            VideoItem(
                source="zg01",
                id=vid,
                name=name.strip(),
                cover=cover,
                note=_node_text(card.select_one(".module-item-note")),
                type=_node_text(card.select_one(".module-card-item-class")),
                year=year_match.group(0) if year_match else "",
            )
        )
    return items[:40]


def _player_data(html: str) -> dict[str, Any]:
    m = re.search(r"player_aaaa\s*=\s*(\{.*?\})\s*</script>", html, re.S) or re.search(
        r"player_aaaa\s*=\s*(\{.*?\});", html, re.S
    )
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:
        return {}


def _is_playable_url(url: str) -> bool:
    return ".m3u8" in url or url.lower().endswith(".mp4")


def _line_probe(vid: str, line: int) -> dict[str, Any]:
    try:
        html = http_get(f"{ZG01_BASE}/vodplay/{vid}-{line}-1.html", referer=ZG01_BASE + "/")
        data = _player_data(html)
        return {"from": data.get("from", ""), "playable": _is_playable_url(data.get("url", ""))}
    except Exception:
        return {"from": "", "playable": False}


def detail_zg01(vid: str) -> dict[str, Any]:
    from collections import OrderedDict

    html = http_get(f"{ZG01_BASE}/voddetail/{vid}.html", referer=ZG01_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    name = _node_text(soup.select_one("h1"))
    cover = _img_src(soup.select_one(".module-item-pic img")) or _img_src(soup.select_one("img.lazyload"))
    desc = _node_text(
        soup.select_one(".module-info-introduction-content")
        or soup.select_one(".vod_content")
        or soup.select_one("[class*=introduction]")
    )

    grouped: "OrderedDict[int, dict[int, str]]" = OrderedDict()
    for a in soup.select(f'a[href*="/vodplay/{vid}-"]'):
        mm = re.search(rf"/vodplay/{vid}-(\d+)-(\d+)\.html", a.get("href", ""))
        if not mm:
            continue
        li, ep = int(mm.group(1)), int(mm.group(2))
        grouped.setdefault(li, {})
        if ep not in grouped[li]:
            grouped[li][ep] = _node_text(a)

    lines: list[dict[str, Any]] = []
    if grouped:
        with ThreadPoolExecutor(max_workers=min(4, len(grouped))) as pool:
            probes = {li: pool.submit(_line_probe, vid, li) for li in grouped}
            for li, eps in grouped.items():
                try:
                    meta = probes[li].result()
                except Exception:
                    meta = {"from": "", "playable": False}
                episodes = [
                    {"ep": ep, "name": (eps[ep] if eps[ep] and eps[ep] != "立即播放" else f"第{ep}集")}
                    for ep in sorted(eps)
                ]
                lines.append(
                    {
                        "line": li,
                        "from": meta.get("from", ""),
                        "playable": meta.get("playable", False),
                        "count": len(episodes),
                        "episodes": episodes,
                    }
                )

    return {
        "source": "zg01",
        "id": vid,
        "name": name,
        "cover": cover,
        "desc": desc,
        "pageUrl": f"{ZG01_BASE}/voddetail/{vid}.html",
        "lines": lines,
    }


def parse_zg01(vid: str, line: int, ep: int) -> dict[str, Any]:
    page_url = f"{ZG01_BASE}/vodplay/{vid}-{line}-{ep}.html"
    data = _player_data(http_get(page_url, referer=ZG01_BASE + "/"))
    url = data.get("url", "")
    return {
        "from": data.get("from", ""),
        "url": url,
        "isM3u8": ".m3u8" in url,
        "isMp4": url.lower().endswith(".mp4"),
        "playable": _is_playable_url(url),
        "pageUrl": page_url,
    }


# ---- 免费采集 API（MacCMS JSON，?ac=detail&wd= 直接返回含 m3u8 的播放地址）----
VIDEO_API_SITES = [
    {"key": "ffzy", "name": "非凡", "api": "https://ffzy5.tv/api.php/provide/vod"},
    {"key": "bfzy", "name": "暴风", "api": "https://bfzyapi.com/api.php/provide/vod"},
    {"key": "rycj", "name": "如意", "api": "https://cj.rycjapi.com/api.php/provide/vod"},
    {"key": "dyttzy", "name": "天堂", "api": "http://caiji.dyttzyapi.com/api.php/provide/vod"},
    {"key": "jisu", "name": "极速", "api": "https://jszyapi.com/api.php/provide/vod"},
    {"key": "zuid", "name": "最大", "api": "https://api.zuidapi.com/api.php/provide/vod"},
]
API_SITE_BY_KEY = {s["key"]: s for s in VIDEO_API_SITES}


def _video_api_get(api: str, params: dict[str, Any]) -> dict[str, Any]:
    if HAS_CFFI:
        resp = cffi_requests.get(
            api, params=params, headers={"User-Agent": UA}, impersonate="chrome124", timeout=15, verify=False
        )
        return resp.json()
    import ssl

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"{api}?{urllib.parse.urlencode(params)}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def search_maccms(site: dict[str, str], keyword: str) -> list[VideoItem]:
    try:
        data = _video_api_get(site["api"], {"ac": "detail", "wd": keyword, "pg": 1})
    except Exception:
        return []
    items: list[VideoItem] = []
    for v in (data.get("list") or [])[:20]:
        vid = str(v.get("vod_id", "")).strip()
        if not vid:
            continue
        items.append(
            VideoItem(
                source=site["key"],
                id=vid,
                name=(v.get("vod_name") or "").strip(),
                cover=(v.get("vod_pic") or "").strip(),
                note=(v.get("vod_remarks") or "").strip(),
                type=(v.get("type_name") or "").strip(),
                year=str(v.get("vod_year") or "").strip(),
                area=(v.get("vod_area") or "").strip(),
            )
        )
    return items


def _parse_maccms_play(vod: dict[str, Any]) -> list[dict[str, Any]]:
    froms = (vod.get("vod_play_from") or "").split("$$$")
    groups = (vod.get("vod_play_url") or "").split("$$$")
    lines: list[dict[str, Any]] = []
    for i, group in enumerate(groups):
        episodes: list[dict[str, Any]] = []
        for idx, seg in enumerate(group.split("#"), 1):
            seg = seg.strip()
            if not seg:
                continue
            name, url = seg.split("$", 1) if "$" in seg else (f"第{idx}集", seg)
            url = url.strip()
            if not url:
                continue
            episodes.append({"ep": len(episodes) + 1, "name": name.strip() or f"第{idx}集",
                             "url": url, "playable": ".m3u8" in url})
        if not episodes:
            continue
        lines.append({
            "line": i + 1,
            "from": (froms[i] if i < len(froms) else f"线路{i + 1}").strip(),
            "playable": any(e["playable"] for e in episodes),
            "count": len(episodes),
            "episodes": episodes,
        })
    return lines


def detail_maccms(site: dict[str, str], vid: str) -> dict[str, Any]:
    data = _video_api_get(site["api"], {"ac": "detail", "ids": vid})
    lst = data.get("list") or []
    if not lst:
        raise RuntimeError("未找到该影视详情")
    v = lst[0]
    desc = re.sub(r"<[^>]+>", "", v.get("vod_blurb") or v.get("vod_content") or "").strip()
    return {
        "source": site["key"],
        "id": str(vid),
        "name": (v.get("vod_name") or "").strip(),
        "cover": (v.get("vod_pic") or "").strip(),
        "desc": desc,
        "pageUrl": "",
        "lines": _parse_maccms_play(v),
    }


def _proxy_seg(absolute_url: str, referer: str) -> str:
    return "/api/video/stream?" + urllib.parse.urlencode({"url": absolute_url, "referer": referer})


def _rewrite_m3u8(text: str, base_url: str, referer: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue
        if stripped.startswith("#"):
            if "URI=" in stripped:
                stripped = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: 'URI="' + _proxy_seg(urllib.parse.urljoin(base_url, m.group(1)), referer) + '"',
                    stripped,
                )
            out.append(stripped)
            continue
        out.append(_proxy_seg(urllib.parse.urljoin(base_url, stripped), referer))
    return "\n".join(out)


@app.get("/api/video/sites")
def api_video_sites():
    return jsonify({"sites": VIDEO_SITES})


@app.get("/api/video/search")
def api_video_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "缺少搜索关键词"}), 400
    blocked = [s for s in VIDEO_SITES if not s["scrapable"]]

    tasks: dict[str, Any] = {"zg01": (lambda: search_zg01(q))}
    for s in VIDEO_API_SITES:
        tasks[s["key"]] = (lambda site=s: search_maccms(site, q))

    results: dict[str, list[VideoItem]] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {k: pool.submit(fn) for k, fn in tasks.items()}
        for k, f in futs.items():
            try:
                results[k] = f.result()
            except Exception:
                results[k] = []

    # 交错合并各来源结果，避免单一来源刷屏
    ordered_keys = ["zg01"] + [s["key"] for s in VIDEO_API_SITES]
    items: list[VideoItem] = []
    i = 0
    while True:
        added = False
        for k in ordered_keys:
            lst = results.get(k, [])
            if i < len(lst):
                items.append(lst[i])
                added = True
        if not added:
            break
        i += 1

    sources = [{"key": "zg01", "name": "zg01"}] + [{"key": s["key"], "name": s["name"]} for s in VIDEO_API_SITES]
    return jsonify(
        {"query": q, "count": len(items), "items": [asdict(x) for x in items], "blocked": blocked, "sources": sources}
    )


@app.get("/api/video/detail")
def api_video_detail():
    vid = (request.args.get("id") or "").strip()
    source = (request.args.get("source") or "zg01").lower()
    if not vid:
        return jsonify({"error": "缺少 id"}), 400
    try:
        if source == "zg01":
            return jsonify(detail_zg01(vid))
        site = API_SITE_BY_KEY.get(source)
        if not site:
            return jsonify({"error": f"暂不支持来源: {source}"}), 400
        return jsonify(detail_maccms(site, vid))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/parse")
def api_video_parse():
    vid = (request.args.get("id") or "").strip()
    source = (request.args.get("source") or "zg01").lower()
    if not vid:
        return jsonify({"error": "缺少 id"}), 400
    if source != "zg01":
        return jsonify({"error": f"暂不支持来源: {source}"}), 400
    try:
        return jsonify(parse_zg01(vid, int(request.args.get("line", "1")), int(request.args.get("ep", "1"))))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.get("/api/video/stream")
def api_video_stream():
    target = request.args.get("url", "")
    referer = request.args.get("referer", ZG01_BASE + "/")
    if not (target.startswith("http://") or target.startswith("https://")):
        return jsonify({"error": "无效链接"}), 400
    hdrs = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        hdrs["Referer"] = referer
    try:
        if HAS_CFFI:
            resp = cffi_requests.get(target, headers=hdrs, impersonate="chrome124", timeout=30)
            status, content, ctype = resp.status_code, resp.content, resp.headers.get("Content-Type", "")
        else:
            req = urllib.request.Request(target, headers=hdrs)
            with urllib.request.urlopen(req, timeout=30) as resp:
                status, content, ctype = resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except Exception as exc:
        return jsonify({"error": f"拉取失败: {exc}"}), 502

    if status == 200 and content.lstrip(b"\xef\xbb\xbf").lstrip()[:7] == b"#EXTM3U":
        rewritten = _rewrite_m3u8(content.decode("utf-8", "ignore"), target, referer)
        return Response(rewritten, status=status, mimetype="application/vnd.apple.mpegurl")
    return Response(content, status=status, content_type=ctype or "application/octet-stream")


# ===================== 电子书 (annas-archive / xiaolipan / xiunews + 原站入口) =====================

ANNAS_BASE = "https://zh.annas-archive.gl"
XIAOLIPAN_BASE = "https://www.xiaolipan.com"
XIUNEWS_BASE = "http://www.xiunews.com"

# 可服务端抓取（search/detail）的来源 + 仅提供「打开原站」入口的来源。
# scrapable=True 走聚合搜索；kind=read 支持站内在线阅读，kind=download 仅提供下载/原站。
BOOK_SOURCES = [
    {
        "key": "annas",
        "name": "安娜的档案 Anna's Archive",
        "url": ANNAS_BASE + "/",
        "scrapable": True,
        "kind": "download",
        "search": ANNAS_BASE + "/search?q={kw}",
        "note": "海量电子书检索（PDF/EPUB/MOBI…），下载跳转原站",
    },
    {
        "key": "xiaolipan",
        "name": "小璃盘",
        "url": XIAOLIPAN_BASE + "/",
        "scrapable": True,
        "kind": "download",
        "search": XIAOLIPAN_BASE + "/search.html?keyword={kw}",
        "note": "kindle/PDF/txt/mobi/epub 电子书下载",
    },
    {
        "key": "xiunews",
        "name": "笔趣阁 xiunews",
        "url": XIUNEWS_BASE + "/",
        "scrapable": True,
        "kind": "read",
        "search": XIUNEWS_BASE + "/modules/article/search.php?searchkey={kw}",
        "search_charset": "gbk",
        "note": "小说站，支持站内在线阅读 / 下载 TXT（部分 CDN 拦机房 IP，住宅宽带更稳）",
    },
    {
        "key": "dushupai",
        "name": "读书派 dushupai.com",
        "url": "https://www.dushupai.com/",
        "scrapable": False,
        "note": "Cloudflare 人机验证，服务器端无法抓取，请打开原站搜索",
    },
    {
        "key": "feiku6",
        "name": "飞库 feiku6.com",
        "url": "https://feiku6.com/",
        "scrapable": False,
        "note": "Cloudflare 人机验证，服务器端无法抓取，请打开原站搜索",
    },
    {
        "key": "lunarora",
        "name": "Lunarora lunarora.com",
        "url": "https://lunarora.com/",
        "scrapable": False,
        "note": "Cloudflare 人机验证，服务器端无法抓取，请打开原站搜索",
    },
    {
        "key": "shidianguji",
        "name": "识典古籍 shidianguji.com",
        "url": "https://www.shidianguji.com/",
        "scrapable": False,
        "search": "https://www.shidianguji.com/search?q={kw}",
        "note": "动态站点 + 反爬，服务器端无法抓取，请打开原站搜索",
    },
]
BOOK_SOURCE_BY_KEY = {s["key"]: s for s in BOOK_SOURCES}


@dataclass
class BookItem:
    source: str
    id: str
    title: str
    author: str = ""
    cover: str = ""
    meta: str = ""
    ext: str = ""
    url: str = ""
    kind: str = "download"


def http_get_bytes(url: str, headers: dict | None = None, referer: str | None = None, timeout: int = 20) -> bytes:
    hdrs = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        hdrs["Referer"] = referer
    if headers:
        hdrs.update(headers)
    if HAS_CFFI:
        resp = cffi_requests.get(url, headers=hdrs, impersonate="chrome124", timeout=timeout)
        resp.raise_for_status()
        return resp.content
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _book_search_url(site: dict[str, Any], keyword: str) -> str:
    tpl = site.get("search")
    if tpl:
        # 个别站点的搜索参数需用特定编码（如笔趣阁 search.php 期望 GBK），否则原站也搜不到。
        charset = site.get("search_charset")
        kw = keyword.encode(charset, "ignore") if charset else keyword
        return tpl.replace("{kw}", urllib.parse.quote(kw))
    return site["url"]


def _title_matches(keyword: str, title: str) -> bool:
    """关键词相关性过滤：标题需包含关键词（忽略大小写/空格）；多词查询需包含全部词。"""
    k = (keyword or "").strip().lower()
    t = (title or "").lower()
    if not k:
        return True
    if re.sub(r"\s+", "", k) in re.sub(r"\s+", "", t):
        return True
    tokens = [tok for tok in re.split(r"\s+", k) if tok]
    if len(tokens) > 1 and all(tok in t for tok in tokens):
        return True
    return False


# ---------------- Anna's Archive ----------------

def _annas_img(row: Any) -> str:
    for img in row.find_all("img"):
        src = (img.get("data-src") or img.get("src") or "").strip()
        if src.startswith("http"):
            return src
    return ""


def search_annas(keyword: str) -> list[BookItem]:
    html = http_get(f"{ANNAS_BASE}/search?q={urllib.parse.quote(keyword)}", referer=ANNAS_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    items: list[BookItem] = []
    seen: set[str] = set()
    for a in soup.select('a[href^="/md5/"]'):
        href = a.get("href", "")
        m = re.match(r"^/md5/([0-9a-f]{32})$", href)
        if not m:
            continue
        title = a.get_text(strip=True)
        if not title:  # cover anchors have no text
            continue
        md5 = m.group(1)
        if md5 in seen:
            continue
        seen.add(md5)
        # 向上找包含封面图的整条记录容器（标题在文本列里，封面图在同级另一列）。
        row = a
        for _ in range(8):
            row = row.parent
            if row is None:
                break
            if row.name == "div" and _annas_img(row):
                break
        meta = ""
        ext = ""
        cover = ""
        if row is not None:
            md = row.find("div", class_=lambda c: c and "text-gray-800" in c)
            if md:
                meta = re.sub(r"\s+", " ", md.get_text(" ", strip=True))
                fm = re.search(r"\b(PDF|EPUB|MOBI|AZW3|TXT|DJVU|CBZ|CBR|FB2)\b", meta, re.I)
                if fm:
                    ext = fm.group(1).lower()
            cover = _annas_img(row)
        items.append(
            BookItem(source="annas", id=md5, title=title, cover=cover, meta=meta, ext=ext,
                     url=f"{ANNAS_BASE}/md5/{md5}", kind="download")
        )
        if len(items) >= 30:
            break
    return items


def detail_annas(md5: str) -> dict[str, Any]:
    page = f"{ANNAS_BASE}/md5/{md5}"
    html = http_get(page, referer=ANNAS_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    title = ""
    if soup.title:
        title = soup.title.get_text(strip=True)
        for sep in (" - ", " — ", " | "):
            if sep in title:
                title = title.rsplit(sep, 1)[0].strip()
                break
    if not title:
        h = soup.find("h1")
        title = h.get_text(strip=True) if h else ""
    img = soup.find("img", src=re.compile(r"^https?://"))
    cover = img.get("src").strip() if img else ""
    desc = ""
    dd = soup.find("div", class_=lambda c: c and "js-md5-top-box-description" in c)
    if dd:
        desc = dd.get_text(" ", strip=True)
    # 下载入口：原站会列出若干镜像 / 慢速下载（多需排队或会员），统一跳转原站下载页
    downloads = [{"label": "前往安娜的档案下载页（含多镜像/慢速下载）", "url": page, "kind": "page"}]
    return {
        "source": "annas",
        "id": md5,
        "title": title,
        "cover": cover,
        "desc": desc[:600],
        "pageUrl": page,
        "kind": "download",
        "downloads": downloads,
    }


# ---------------- 小璃盘 xiaolipan ----------------

def search_xiaolipan(keyword: str) -> list[BookItem]:
    url = f"{XIAOLIPAN_BASE}/search.html?keyword={urllib.parse.quote(keyword)}"
    html = http_get(url, referer=XIAOLIPAN_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    items: list[BookItem] = []
    seen: set[str] = set()
    for h in soup.select("h2.entry-title a"):
        href = h.get("href", "")
        m = re.search(r"/p/(\d+)\.html", href)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen:
            continue
        seen.add(pid)
        title = h.get_text(strip=True)
        art = h.find_parent("article")
        cover = ""
        if art:
            img = art.find("img")
            if img:
                cover = (img.get("data-original") or img.get("src") or "").strip()
        items.append(
            BookItem(source="xiaolipan", id=pid, title=title, cover=cover,
                     url=f"{XIAOLIPAN_BASE}/p/{pid}.html", kind="download")
        )
        if len(items) >= 30:
            break
    return items


def detail_xiaolipan(pid: str) -> dict[str, Any]:
    page = f"{XIAOLIPAN_BASE}/p/{pid}.html"
    html = http_get(page, referer=XIAOLIPAN_BASE + "/")
    soup = BeautifulSoup(html, "lxml")
    title = ""
    h = soup.find("h1")
    if h:
        title = h.get_text(strip=True)
    if not title:
        mt = re.search(r'<title>([^<_]+)', html)
        title = mt.group(1).strip() if mt else ""
    img = soup.find("meta", property="og:image")
    cover = img.get("content", "").strip() if img else ""
    desc = ""
    dm = soup.find("meta", attrs={"name": "description"})
    if dm:
        desc = dm.get("content", "").strip()
    m = re.search(r'download_url:\s*"([^"]+)"', html)
    download_url = m.group(1) if m else ""
    me = re.search(r"enabledDownload:\s*(\w+)", html)
    enabled = bool(me and me.group(1) == "true")
    downloads: list[dict[str, str]] = []
    if download_url and enabled:
        downloads.append({"label": "前往下载页（kindle/PDF/EPUB/MOBI/TXT）", "url": download_url, "kind": "page"})
    downloads.append({"label": "打开原站书籍页面", "url": page, "kind": "page"})
    return {
        "source": "xiaolipan",
        "id": pid,
        "title": title,
        "cover": cover,
        "desc": desc[:600],
        "pageUrl": page,
        "kind": "download",
        "downloads": downloads,
    }


# ---------------- 笔趣阁 xiunews（UTF-8，支持在线阅读 / 下载 TXT） ----------------

# 笔趣阁用**普通 urllib** 抓取，不走 curl_cffi。
# 实测：curl_cffi（chrome 指纹模拟）连本站会稳定 `curl (28) Connection timed out`，
# 用户住宅网络与机房 VM 均复现；而普通 HTTP 请求是通的。本站是纯 http 站，不需要
# TLS 指纹模拟。用一个带 cookiejar 的 opener 复用 cookie（替代原来的「预热会话」）。
_xiunews_cookiejar = http.cookiejar.CookieJar()
_xiunews_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_xiunews_cookiejar))
_xiunews_warmed = False
_xiunews_lock = threading.Lock()


def _xiunews_warm_session(timeout: int = 6) -> None:
    """尽力访问一次首页拿 cookie（best-effort）。失败不致命，且只付一次；
    用独立的短超时，绝不占用后续搜索的预算。"""
    global _xiunews_warmed
    with _xiunews_lock:
        if _xiunews_warmed:
            return
        try:
            req = urllib.request.Request(
                XIUNEWS_BASE + "/",
                headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            _xiunews_opener.open(req, timeout=timeout).read()
        except Exception:
            pass  # 拿不到 cookie 也无妨：search.php 多数情况下不强制要求
        _xiunews_warmed = True  # 失败也标记，避免每次搜索都重复付这笔超时


def _xiunews_reset_session() -> None:
    """清空 cookie 并允许下次重新预热（用于连接超时/被拦后的重试）。"""
    global _xiunews_warmed
    with _xiunews_lock:
        _xiunews_cookiejar.clear()
        _xiunews_warmed = False


def _xiunews_decode(raw: bytes) -> str:
    """站点页面为 GBK；个别页可能是 UTF-8，做自适应解码。"""
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", "ignore")


def _xiunews_fetch(url: str, timeout: int = 15, retries: int = 2,
                   data: bytes | None = None, content_type: str | None = None) -> tuple[str, str]:
    """抓取笔趣阁页面，返回 (解码后的 html, 最终 url)。用普通 urllib + cookiejar。

    data 非空时改用 POST（笔趣阁 jieqi 搜索引擎通常认 POST 表单）。

    偶发连接超时（curl/urllib timed out）时做有限次重试，失败后清 cookie 再试，
    最大限度降低「章节加载失败: Connection timed out」的概率。
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            headers = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9",
                       "Referer": XIUNEWS_BASE + "/"}
            if content_type:
                headers["Content-Type"] = content_type
            req = urllib.request.Request(
                url, data=data, headers=headers,
                method="POST" if data is not None else "GET")
            with _xiunews_opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                final = resp.geturl() or url
            return _xiunews_decode(raw), final
        except Exception as exc:
            last_exc = exc
            # 超时 / 连接失败多半是线路问题，清 cookie 重新预热后再试一次。
            _xiunews_reset_session()
            if attempt < retries:
                time.sleep(0.6)
    raise last_exc if last_exc else RuntimeError("xiunews fetch failed")


def _xiunews_html(url: str, timeout: int = 15, retries: int = 2) -> str:
    return _xiunews_fetch(url, timeout=timeout, retries=retries)[0]


def _parse_xiunews_results(html: str, final_url: str, keyword: str) -> list[BookItem]:
    """解析笔趣阁搜索结果页：每行含书名链接 /NN_NNNN/ 与作者。"""
    soup = BeautifulSoup(html, "lxml")
    items: list[BookItem] = []
    seen: set[str] = set()
    for a in soup.select('a[href*="_"]'):
        href = a.get("href", "")
        m = re.search(r"/(\d+_\d+)/?$", href)
        if not m:
            continue
        bid = m.group(1)
        title = a.get_text(strip=True)
        if not title or bid in seen:
            continue
        seen.add(bid)
        author = ""
        row = a.find_parent("tr")
        if row:
            tds = row.find_all("td")
            if len(tds) >= 3:
                author = tds[2].get_text(strip=True)
        items.append(
            BookItem(source="xiunews", id=bid, title=title, author=author,
                     url=f"{XIUNEWS_BASE}/{bid}/", kind="read")
        )
        if len(items) >= 30:
            break
    # 单一匹配时站点会 302 直接跳到书页（/NN_NNNN/），此时表格为空，从最终 url 兜底取书
    if not items:
        m = re.search(r"/(\d+_\d+)/?$", final_url or "")
        if m:
            bid = m.group(1)
            h1 = soup.find("h1")
            title = h1.get_text(strip=True) if h1 else keyword
            ma = re.search(r"作者[:：]\s*([^<\s]+)", html)
            author = ma.group(1).strip() if ma else ""
            items.append(
                BookItem(source="xiunews", id=bid, title=title, author=author,
                         url=f"{XIUNEWS_BASE}/{bid}/", kind="read")
            )
    return items


def _xiunews_cover(bid: str) -> str:
    """抓取书页 og:image 作为封面（搜索结果页本身不含封面图）。"""
    try:
        html = _xiunews_html(f"{XIUNEWS_BASE}/{bid}/", timeout=8, retries=0)
    except Exception:
        return ""
    soup = BeautifulSoup(html, "lxml")
    img = soup.find("meta", property="og:image")
    return img.get("content", "").strip() if img and img.get("content") else ""


def enrich_xiunews_covers(items: list[BookItem], deadline: float = 9.0) -> None:
    """并发补全笔趣阁搜索结果的封面（搜索页无图，需访问各书页取 og:image）。

    尽力而为：受 deadline 约束，超时未取到的书保留空封面（前端回退到占位首字），
    避免个别慢书页拖垮整个搜索请求。
    """
    pending = [it for it in items if not it.cover]
    if not pending:
        return
    pool = ThreadPoolExecutor(max_workers=min(8, len(pending)))
    try:
        futures = {pool.submit(_xiunews_cover, it.id): it for it in pending}
        try:
            for future in as_completed(futures, timeout=deadline):
                item = futures[future]
                try:
                    item.cover = future.result() or item.cover
                except Exception:
                    pass
        except TimeoutError:
            pass  # 超出整体期限，放弃尚未完成的封面抓取
    finally:
        pool.shutdown(wait=False)


# 单源在 /api/book/search 聚合里的整体预算（见 f.result(timeout=...)）。
XIUNEWS_BUDGET = 20.0


SEARCH_PHP = XIUNEWS_BASE + "/modules/article/search.php"


def _xiunews_search_once(keyword: str, method: str, charset: str, timeout: int) -> list[BookItem]:
    """按指定 method/charset 跑一次站内搜索并解析结果。"""
    kw = keyword.encode(charset, "ignore")
    if method == "POST":
        body = ("searchkey=" + urllib.parse.quote(kw)).encode("ascii")
        html, final_url = _xiunews_fetch(
            SEARCH_PHP, timeout=timeout, retries=0, data=body,
            content_type=f"application/x-www-form-urlencoded; charset={charset}")
    else:
        url = f"{SEARCH_PHP}?searchkey={urllib.parse.quote(kw)}"
        html, final_url = _xiunews_fetch(url, timeout=timeout, retries=0)
    return _parse_xiunews_results(html, final_url, keyword)


# 笔趣阁用的是 jieqi 引擎：搜索通常认 **POST + GBK** 表单；个别镜像/线路下 GET 或
# UTF-8 才出结果。历史上每次只用一种方式，换一次就「又搜不到」，故这里按可靠性顺序
# 依次尝试，取第一种能解析出结果的方式。
XIUNEWS_STRATEGIES = [("POST", "gbk"), ("GET", "gbk"), ("GET", "utf-8")]


def _xiunews_search_round(keyword: str, deadline: float) -> list[BookItem]:
    """按 POST(GBK)→GET(GBK)→GET(UTF-8) 跑一轮，命中即返回。

    关键：给每个策略**公平的超时切片**（按剩余预算在尚未尝试的策略间均分），避免靠前的
    慢策略把预算吃光、让原本能出结果的回退策略根本没机会跑——这正是「结果又消失」的根因之一。
    """
    n = len(XIUNEWS_STRATEGIES)
    for idx, (method, charset) in enumerate(XIUNEWS_STRATEGIES):
        left = deadline - time.monotonic()
        if left <= 2.0:
            break
        # 在剩余预算里按「尚未尝试的策略数」公平均分，再夹到 [3s, 8s]，最后绝不超出剩余预算。
        per = min(8.0, max(3.0, left / (n - idx)))
        per = min(per, left - 0.5)
        timeout = int(round(per))
        if timeout < 2:
            # 预算已不够给一个像样的切片：剩多少用多少（>=2s），仍给最后的策略一次机会，
            # 绝不把负数/0 传给底层（旧 bug：负超时让真正能用的策略直接报错）。
            timeout = max(2, int(left - 0.2))
        if timeout < 2:
            break
        try:
            items = _xiunews_search_once(keyword, method, charset, timeout=timeout)
        except Exception:
            items = []  # 该策略失败（超时/被拦/方法不被接受）：继续尝试下一种
        if items:
            return items
    return []


def search_xiunews(keyword: str) -> list[BookItem]:
    # 预热放在计时**之前**：它有自己的短超时，绝不能占用搜索预算（旧逻辑里预热超时
    # 吃掉 10s，导致后面策略拿到负数超时而全灭）。
    _xiunews_warm_session()
    start = time.monotonic()
    deadline = start + min(18.0, XIUNEWS_BUDGET)
    items = _xiunews_search_round(keyword, deadline)
    if not items:
        # 一轮全空多半是会话 cookie 失效被「200 空结果」软拦——重置会话，下次搜索会重新
        # 预热拿新 cookie，避免「结果永久消失直到重启」。
        _xiunews_reset_session()

    # 封面补全只用「剩余预算」，绝不挤占出结果的时间：搜索慢时少补甚至不补，但结果照常返回。
    remaining = XIUNEWS_BUDGET - (time.monotonic() - start)
    if items and remaining >= 2.0:
        try:
            enrich_xiunews_covers(items, deadline=min(8.0, remaining))
        except Exception:
            pass  # 补封面失败绝不能影响搜索结果
    return items


def detail_xiunews(bid: str) -> dict[str, Any]:
    page = f"{XIUNEWS_BASE}/{bid}/"
    # 站点偶发拦机房/本机 IP，首次请求常超时；重试时会重置并重新预热会话（拿新 cookie），
    # 这正是让详情能稳定加载的关键。仅在重试耗尽后才由路由回退到「原站查看目录」。
    html = _xiunews_html(page, timeout=12, retries=2)
    soup = BeautifulSoup(html, "lxml")
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    author = ""
    ma = re.search(r"作者[:：]\s*([^<\s]+)", html)
    if ma:
        author = ma.group(1).strip()
    img = soup.find("meta", property="og:image")
    cover = img.get("content", "").strip() if img else ""
    desc = ""
    dm = soup.find("meta", property="og:description") or soup.find("meta", attrs={"name": "description"})
    if dm:
        desc = dm.get("content", "").strip()
    chapters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for a in soup.select("dd a, .listmain a, #list a"):
        href = a.get("href", "")
        m = re.search(rf"/{re.escape(bid)}/(\d+)\.html", href)
        if not m:
            continue
        cid = m.group(1)
        if cid in seen:
            continue
        seen.add(cid)
        chapters.append({"id": cid, "name": a.get_text(strip=True), "url": f"{XIUNEWS_BASE}/{bid}/{cid}.html"})
    return {
        "source": "xiunews",
        "id": bid,
        "title": title,
        "author": author,
        "cover": cover,
        "desc": desc[:600],
        "pageUrl": page,
        "kind": "read",
        "chapters": chapters,
    }


def _xiunews_chapter_text(bid: str, cid: str) -> tuple[str, list[str]]:
    # 章节页允许一次重试（重置会话后再试），抓起偶发超时；仍耗尽才回退到「重试本章 / 原站阅读」。
    html = _xiunews_html(f"{XIUNEWS_BASE}/{bid}/{cid}.html", timeout=12, retries=1)
    soup = BeautifulSoup(html, "lxml")
    name = ""
    h1 = soup.find("h1")
    if h1:
        name = h1.get_text(strip=True)
    content_el = soup.find(id="content") or soup.find("div", class_=re.compile(r"content|showtxt"))
    paras: list[str] = []
    if content_el:
        for tag in content_el.find_all(["script", "style", "div", "a"]):
            tag.decompose()
        raw = content_el.get_text("\n", strip=True)
        for line in raw.split("\n"):
            line = line.strip()
            if not line or "笔趣" in line and "http" in line:
                continue
            paras.append(line)
    return name, paras


def chapter_xiunews(bid: str, cid: str) -> dict[str, Any]:
    name, paras = _xiunews_chapter_text(bid, cid)
    return {"source": "xiunews", "book": bid, "id": cid, "name": name, "paragraphs": paras}


# ---------------- 电子书路由 ----------------

@app.get("/api/book/sources")
def api_book_sources():
    return jsonify({"sources": BOOK_SOURCES})


@app.get("/api/book/search")
def api_book_search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "缺少搜索关键词"}), 400

    scrapers = {
        "annas": (lambda: search_annas(q)),
        "xiaolipan": (lambda: search_xiaolipan(q)),
        "xiunews": (lambda: search_xiunews(q)),
    }
    results: dict[str, list[BookItem]] = {}
    with ThreadPoolExecutor(max_workers=len(scrapers)) as pool:
        futs = {k: pool.submit(fn) for k, fn in scrapers.items()}
        for k, f in futs.items():
            try:
                results[k] = f.result(timeout=22)
            except Exception:
                results[k] = []

    # 过滤掉标题与关键词无关的结果（部分源会返回热门/无关书目）
    for k in results:
        results[k] = [b for b in results[k] if _title_matches(q, b.title)]

    ordered = ["annas", "xiaolipan", "xiunews"]
    items: list[BookItem] = []
    i = 0
    while True:
        added = False
        for k in ordered:
            lst = results.get(k, [])
            if i < len(lst):
                items.append(lst[i])
                added = True
        if not added:
            break
        i += 1

    blocked = [
        {"key": s["key"], "name": s["name"], "url": _book_search_url(s, q), "note": s.get("note", "")}
        for s in BOOK_SOURCES
        if not s["scrapable"]
    ]
    sources = [{"key": s["key"], "name": s["name"], "kind": s.get("kind", "")} for s in BOOK_SOURCES if s["scrapable"]]
    return jsonify(
        {"query": q, "count": len(items), "items": [asdict(x) for x in items], "blocked": blocked, "sources": sources}
    )


@app.get("/api/book/detail")
def api_book_detail():
    source = (request.args.get("source") or "").lower()
    bid = (request.args.get("id") or "").strip()
    if not source or not bid:
        return jsonify({"error": "缺少 source 或 id"}), 400
    try:
        if source == "annas":
            return jsonify(detail_annas(bid))
        if source == "xiaolipan":
            return jsonify(detail_xiaolipan(bid))
        if source == "xiunews":
            return jsonify(detail_xiunews(bid))
        return jsonify({"error": f"暂不支持来源: {source}"}), 400
    except Exception as exc:
        text = str(exc).lower()
        if source == "xiunews" and ("timed out" in text or "curl: (28)" in text or "timeout" in text):
            msg = "详情加载超时：笔趣阁偶发拦截服务器/本机 IP。可重试，或点下方按钮在原站查看目录。"
            return jsonify({"error": msg, "pageUrl": f"{XIUNEWS_BASE}/{bid}/"}), 502
        page_url = f"{XIUNEWS_BASE}/{bid}/" if source == "xiunews" else ""
        return jsonify({"error": f"详情加载失败：{exc}", "pageUrl": page_url}), 500


@app.get("/api/book/chapter")
def api_book_chapter():
    source = (request.args.get("source") or "").lower()
    book = (request.args.get("book") or "").strip()
    cid = (request.args.get("id") or "").strip()
    if source != "xiunews":
        return jsonify({"error": f"该来源不支持站内阅读: {source}"}), 400
    if not book or not cid:
        return jsonify({"error": "缺少 book 或 id"}), 400
    chapter_url = f"{XIUNEWS_BASE}/{book}/{cid}.html"
    try:
        return jsonify(chapter_xiunews(book, cid))
    except Exception as exc:
        text = str(exc).lower()
        if "timed out" in text or "curl: (28)" in text or "timeout" in text:
            msg = "章节加载超时：笔趣阁偶发拦截服务器/本机 IP。可重试，或点下方按钮在原站阅读本章。"
        else:
            msg = f"章节加载失败：{exc}"
        return jsonify({"error": msg, "chapterUrl": chapter_url}), 502


@app.get("/api/book/download")
def api_book_download():
    """笔趣阁整本下载：并发抓取各章节文本，拼成 TXT 返回。"""
    source = (request.args.get("source") or "").lower()
    book = (request.args.get("id") or "").strip()
    if source != "xiunews":
        return jsonify({"error": "该来源请使用原站下载入口"}), 400
    if not book:
        return jsonify({"error": "缺少 id"}), 400
    try:
        detail = detail_xiunews(book)
    except Exception as exc:
        return jsonify({"error": f"获取目录失败: {exc}"}), 502
    chapters = detail.get("chapters", [])
    if not chapters:
        return jsonify({"error": "未找到章节目录"}), 404

    title = detail.get("title") or book
    author = detail.get("author") or ""

    def fetch(ch: dict[str, Any]) -> tuple[str, str]:
        try:
            name, paras = _xiunews_chapter_text(book, ch["id"])
            return ch["id"], (name or ch["name"]) + "\n\n" + "\n".join(paras) + "\n\n"
        except Exception:
            return ch["id"], (ch["name"] or "") + "\n\n（本章加载失败）\n\n"

    order = [c["id"] for c in chapters]
    texts: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch, c): c["id"] for c in chapters}
        for fut in as_completed(futs):
            cid, body = fut.result()
            texts[cid] = body

    def generate() -> Iterator[bytes]:
        header = f"{title}\n作者：{author}\n来源：笔趣阁 {XIUNEWS_BASE}/{book}/\n\n"
        yield header.encode("utf-8")
        for cid in order:
            yield texts.get(cid, "").encode("utf-8")

    filename = sanitize_filename(f"{title}-{author}".strip("-")) + ".txt"
    quoted = urllib.parse.quote(filename)
    headers = {
        "Content-Type": "text/plain; charset=utf-8",
        "Content-Disposition": f"attachment; filename=\"{quoted}\"; filename*=UTF-8''{quoted}",
    }
    return Response(generate(), headers=headers)


@app.get("/api/book/xiunews_diag")
def api_book_xiunews_diag():
    """临时诊断：在「你的网络」上把每种 method×charset 各跑一次站内搜索，
    返回每种方式的耗时/解析行数/书页链接数/示例书目，用于定位「搜索结果消失」。
    用法：浏览器打开 /api/book/xiunews_diag?kw=斗破苍穹 ，把返回的 JSON 贴给开发者。
    """
    kw = (request.args.get("kw") or "斗破苍穹").strip()
    host = urllib.parse.urlparse(XIUNEWS_BASE).hostname or "www.xiunews.com"

    # 1) 原始 TCP 连通性：能不能连上 host:80（区分「网络根本到不了」vs「到得了但搜不到」）。
    tcp: dict[str, Any] = {"host": host, "port": 80}
    t0 = time.monotonic()
    try:
        s = socket.create_connection((host, 80), timeout=8)
        s.close()
        tcp.update({"ok": True})
    except Exception as exc:
        tcp.update({"ok": False, "error": str(exc)})
    tcp["ms"] = int((time.monotonic() - t0) * 1000)

    # 2) 普通 urllib GET 首页（不带 curl_cffi）：验证纯 HTTP 是否可达。
    home: dict[str, Any] = {"transport": "urllib"}
    t0 = time.monotonic()
    try:
        req = urllib.request.Request(XIUNEWS_BASE + "/", headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=8) as resp:
            home.update({"ok": True, "status": resp.status, "bytes": len(resp.read())})
    except Exception as exc:
        home.update({"ok": False, "error": str(exc)})
    home["ms"] = int((time.monotonic() - t0) * 1000)

    # 3) 实际搜索路径（已切换为 urllib + cookiejar）：每种 method×charset 各跑一次。
    _xiunews_warm_session()
    results: list[dict[str, Any]] = []
    for method in ("POST", "GET"):
        for charset in ("gbk", "utf-8"):
            rec: dict[str, Any] = {"method": method, "charset": charset}
            t0 = time.monotonic()
            try:
                kwb = kw.encode(charset, "ignore")
                if method == "POST":
                    body = ("searchkey=" + urllib.parse.quote(kwb)).encode("ascii")
                    html, final_url = _xiunews_fetch(
                        SEARCH_PHP, timeout=10, retries=0, data=body,
                        content_type=f"application/x-www-form-urlencoded; charset={charset}")
                else:
                    url = f"{SEARCH_PHP}?searchkey={urllib.parse.quote(kwb)}"
                    html, final_url = _xiunews_fetch(url, timeout=10, retries=0)
                items = _parse_xiunews_results(html, final_url, kw)
                rec.update({
                    "ok": True,
                    "rows": len(items),
                    "final_url": final_url,
                    "html_len": len(html),
                    "book_links": len(re.findall(r"/\d+_\d+/", html)),
                    "sample": [{"id": it.id, "title": it.title, "author": it.author} for it in items[:5]],
                })
            except Exception as exc:
                rec.update({"ok": False, "error": str(exc)})
            rec["ms"] = int((time.monotonic() - t0) * 1000)
            results.append(rec)
    return jsonify({"kw": kw, "has_cffi": HAS_CFFI, "transport": "urllib",
                    "search_php": SEARCH_PHP, "tcp_connect": tcp,
                    "plain_home_get": home, "results": results})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5178, debug=True)
