#!/usr/bin/env python3
"""Music search/play/download proxy for gequhai, yyfang, fangpi."""

from __future__ import annotations

import base64
import json
import re
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
    try:
        items = search_zg01(q)
    except Exception as exc:
        return jsonify({"query": q, "count": 0, "items": [], "blocked": blocked, "error": f"zg01 搜索失败：{exc}"})
    return jsonify(
        {"query": q, "count": len(items), "items": [asdict(x) for x in items], "blocked": blocked}
    )


@app.get("/api/video/detail")
def api_video_detail():
    vid = (request.args.get("id") or "").strip()
    source = (request.args.get("source") or "zg01").lower()
    if not vid:
        return jsonify({"error": "缺少 id"}), 400
    if source != "zg01":
        return jsonify({"error": f"暂不支持来源: {source}"}), 400
    try:
        return jsonify(detail_zg01(vid))
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5178, debug=True)
