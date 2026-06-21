"""
统一后端服务：
  - 静态文件服务（index.html, leaflet.css/js, jszip.min.js）
  - API 代理（搜索、路线、Overpass、Wikimedia）
  - 语音识别（faster-whisper）
"""
import os
import sys
import json
import time
import tempfile
import threading
import mimetypes
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote

import urllib.request
import urllib.error

# 静态文件目录
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = 'OfflineMapApp/1.0 (educational project)'

# ========== 语音识别（延迟加载） ==========
_model_lock = threading.Lock()
_model = None

def get_model():
    global _model
    with _model_lock:
        if _model is None:
            print('[whisper] 正在加载模型（首次约 1-2 分钟）...', flush=True)
            # 设置 HuggingFace 镜像
            os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
            from faster_whisper import WhisperModel
            _model = WhisperModel('base', device='cpu', compute_type='int8')
            print('[whisper] 模型加载完成', flush=True)
        return _model

def transcribe(audio_path, language='zh'):
    model = get_model()
    segments, _ = model.transcribe(
        audio_path, language=language, beam_size=5,
        vad_filter=True, vad_parameters=dict(min_silence_duration_ms=500)
    )
    return ''.join(seg.text for seg in segments).strip()


# ========== HTTP 代理工具 ==========
def proxy_get(url, timeout=15, user_agent=USER_AGENT):
    """GET 请求代理，返回 (status_code, headers_dict, body_bytes)"""
    req = urllib.request.Request(url, headers={'User-Agent': user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {}, e.read() if e.fp else b''
    except Exception as e:
        return 502, {}, str(e).encode('utf-8')

def proxy_post(url, data, content_type='application/x-www-form-urlencoded', timeout=15, user_agent=USER_AGENT):
    """POST 请求代理"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={
        'User-Agent': user_agent,
        'Content-Type': content_type
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, {}, e.read() if e.fp else b''
    except Exception as e:
        return 502, {}, str(e).encode('utf-8')


# ========== HTTP Handler ==========
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        # ---- API 路由 ----
        if path == '/health':
            return self._json({'ok': True, 'model_loaded': _model is not None})

        # 搜索 API
        if path == '/api/search':
            return self._api_search(qs)

        # 路线规划
        if path == '/api/route':
            return self._api_route(qs)

        # Wikimedia 街景
        if path == '/api/wikimedia':
            return self._api_wikimedia(qs)

        # Overpass 附近地标
        if path == '/api/overpass':
            return self._api_overpass(qs)

        # ---- 静态文件 ----
        return self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/asr':
            return self._handle_asr()

        if path == '/api/overpass':
            # Overpass 也支持 POST（query 在 body 中）
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else b''
            content_type = self.headers.get('Content-Type', '')
            return self._api_overpass_post(body, content_type)

        if path == '/api/download-tiles':
            return self._api_download_tiles()

        return self._json({'error': 'not found'}, 404)

    # ========== API 实现 ==========

    def _api_search(self, qs):
        """统一搜索：智能选择 API，中文优先 Nominatim/Open-Meteo"""
        q = qs.get('q', [''])[0]
        limit = qs.get('limit', ['8'])[0]
        if not q:
            return self._json({'error': 'missing q'}, 400)

        # 检测是否为中文查询（包含中文字符）
        is_chinese = any('\u4e00' <= c <= '\u9fff' for c in q)

        # 搜索提供方：(name, url_builder, transformer, result_getter)
        # 中文顺序：Nominatim → Open-Meteo → Photon
        # 英文顺序：Photon → Nominatim → Open-Meteo
        providers = [
            ('nominatim',
             lambda: f'https://nominatim.openstreetmap.org/search?format=json&q={quote(q)}&limit={limit}&accept-language=zh&addressdetails=1',
             self._transform_nominatim,
             lambda d: d),
            ('open-meteo',
             lambda: f'https://geocoding-api.open-meteo.com/v1/search?name={quote(q)}&count={limit}&language=zh&format=json',
             self._transform_open_meteo,
             lambda d: d.get('results')),
            ('photon',
             lambda: f'https://photon.komoot.io/api/?q={quote(q)}&limit={limit}',
             self._transform_photon,
             lambda d: d.get('features')),
        ]
        if not is_chinese:
            # 英文查询：Photon 优先
            providers = [providers[2], providers[0], providers[1]]

        results = None
        for name, url_builder, transform, get_result in providers:
            try:
                status, _, body = proxy_get(url_builder(), timeout=10)
                if status == 200:
                    data = json.loads(body)
                    raw = get_result(data)
                    if raw:
                        results = transform(data)
                        break
            except Exception as e:
                print(f'[search] {name} error: {e}', flush=True)

        self._json({'results': results or []})

    def _transform_photon(self, data):
        out = []
        for f in data.get('features', []):
            p = f.get('properties', {})
            g = f.get('geometry', {})
            coords = g.get('coordinates', [0, 0])
            ext = p.get('extent')
            out.append({
                'name': p.get('name', ''),
                'display_name': ', '.join(filter(None, [p.get('name'), p.get('district'), p.get('city'), p.get('state'), p.get('country')])),
                'lat': coords[1],
                'lon': coords[0],
                'boundingbox': [ext[1], ext[3], ext[0], ext[2]] if ext else None,
                'type': p.get('osm_value', ''),
                'source': 'photon'
            })
        return out

    def _transform_nominatim(self, data):
        out = []
        for r in data:
            out.append({
                'name': r.get('display_name', '').split(',')[0],
                'display_name': r.get('display_name', ''),
                'lat': float(r.get('lat', 0)),
                'lon': float(r.get('lon', 0)),
                'boundingbox': r.get('boundingbox'),
                'type': r.get('type', ''),
                'source': 'nominatim'
            })
        return out

    def _transform_open_meteo(self, data):
        out = []
        for r in data.get('results', []):
            parts = [r.get('name', '')]
            if r.get('admin1') and r['admin1'] != r.get('name'):
                parts.append(r['admin1'])
            if r.get('country'):
                parts.append(r['country'])
            out.append({
                'name': r.get('name', ''),
                'display_name': ', '.join(parts),
                'lat': r.get('latitude', 0),
                'lon': r.get('longitude', 0),
                'boundingbox': None,
                'type': r.get('feature_code', ''),
                'source': 'open-meteo'
            })
        return out

    def _api_route(self, qs):
        """路线规划代理"""
        profile = qs.get('profile', ['car'])[0]
        from_lng = qs.get('from_lng', [''])[0]
        from_lat = qs.get('from_lat', [''])[0]
        to_lng = qs.get('to_lng', [''])[0]
        to_lat = qs.get('to_lat', [''])[0]
        if not all([from_lng, from_lat, to_lng, to_lat]):
            return self._json({'error': 'missing coords'}, 400)

        url = f'https://router.project-osrm.org/route/v1/{profile}/{from_lng},{from_lat};{to_lng},{to_lat}?overview=full&geometries=geojson&steps=true'
        status, _, body = proxy_get(url, timeout=20)
        if status == 200:
            self._send_raw(200, body, 'application/json')
        else:
            self._json({'error': f'route API returned {status}'}, status)

    def _api_wikimedia(self, qs):
        """Wikimedia Commons 地理搜索"""
        lat = qs.get('lat', [''])[0]
        lon = qs.get('lon', [''])[0]
        radius = qs.get('radius', ['10000'])[0]
        if not lat or not lon:
            return self._json({'error': 'missing lat/lon'}, 400)

        # Step 1: geosearch
        url1 = (
            f'https://commons.wikimedia.org/w/api.php?'
            f'action=query&list=geosearch&gsprimary=all&gsnamespace=6&gslimit=40'
            f'&gsradius={radius}&gscoord={quote(lat)}|{quote(lon)}'
            f'&format=json&origin=*'
        )
        status1, _, body1 = proxy_get(url1, timeout=15)
        if status1 != 200:
            # Wikimedia SSL/网络问题：返回空照片而非错误，让前端优雅降级
            print(f'[wikimedia] geosearch failed: status={status1}', flush=True)
            return self._json({'photos': [], 'error': 'wikimedia unavailable'})

        data1 = json.loads(body1)
        geo_results = (data1.get('query', {}).get('geosearch', []))
        if not geo_results:
            return self._json({'photos': []})

        # Step 2: image info
        pageids = '|'.join(str(x['pageid']) for x in geo_results)
        url2 = (
            f'https://commons.wikimedia.org/w/api.php?'
            f'action=query&pageids={quote(pageids)}'
            f'&prop=imageinfo&iiprop=url|thumburl|dimensions|mime'
            f'&iiurlwidth=800&format=json&origin=*'
        )
        status2, _, body2 = proxy_get(url2, timeout=15)
        if status2 != 200:
            return self._json({'error': 'wikimedia imageinfo failed'}, status2)

        data2 = json.loads(body2)
        pages = data2.get('query', {}).get('pages', {})

        photos = []
        for r in geo_results:
            page = pages.get(str(r['pageid']), {})
            info_list = page.get('imageinfo', [])
            info = info_list[0] if info_list else None
            photos.append({
                'title': r.get('title', ''),
                'name': r.get('title', '').replace('File:', '').rsplit('.', 1)[0],
                'lat': r.get('lat'),
                'lng': r.get('lon'),
                'dist': r.get('dist'),
                'thumb': info.get('thumburl') if info else None,
                'url': info.get('url') if info else None,
                'width': info.get('width') if info else None,
                'height': info.get('height') if info else None
            })

        return self._json({'photos': photos})

    def _api_overpass(self, qs):
        """Overpass 附近地标（GET 方式）"""
        lat = qs.get('lat', [''])[0]
        lon = qs.get('lon', [''])[0]
        radius = qs.get('radius', ['5000'])[0]
        if not lat or not lon:
            return self._json({'error': 'missing lat/lon'}, 400)

        query = (
            f'[out:json][timeout:10];'
            f'(node["tourism"](around:{radius},{lat},{lon});'
            f'node["historic"](around:{radius},{lat},{lon}););'
            f'out body 12;'
        )
        return self._do_overpass(query)

    def _api_overpass_post(self, body, content_type):
        """Overpass POST（原始 query 在 body 中）"""
        # body 可能是 data=... 或纯 query
        if b'data=' in body:
            # form-encoded
            qs = parse_qs(body.decode('utf-8', errors='replace'))
            query = qs.get('data', [''])[0]
        else:
            query = body.decode('utf-8', errors='replace')
        return self._do_overpass(query)

    def _do_overpass(self, query):
        status, _, body = proxy_post(
            'https://overpass-api.de/api/interpreter',
            'data=' + quote(query),
            content_type='application/x-www-form-urlencoded',
            timeout=15
        )
        if status == 200:
            self._send_raw(200, body, 'application/json')
        else:
            self._json({'error': f'overpass returned {status}'}, status)

    def _handle_asr(self):
        try:
            content_type = self.headers.get('Content-Type', '')
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                return self._json({'error': 'empty body'}, 400)

            raw = self.rfile.read(content_length)

            suffix = '.webm'
            if 'ogg' in content_type:
                suffix = '.ogg'
            elif 'wav' in content_type:
                suffix = '.wav'
            elif 'mp4' in content_type or 'm4a' in content_type:
                suffix = '.m4a'

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            try:
                with os.fdopen(tmp_fd, 'wb') as f:
                    f.write(raw)
                print(f'[asr] 收到 {len(raw)} 字节音频', flush=True)

                t0 = time.time()
                text = transcribe(tmp_path, language='zh')
                dt = time.time() - t0
                print(f'[asr] 识别完成: {text!r}  耗时 {dt:.2f}s', flush=True)
                self._json({'text': text, 'time': round(dt, 2)})
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except Exception as e:
            traceback.print_exc()
            self._json({'error': str(e)}, 500)

    def _api_download_tiles(self):
        """多线程下载瓦片并打包为 ZIP"""
        import io
        import zipfile
        from concurrent.futures import ThreadPoolExecutor, as_completed

        try:
            content_length = int(self.headers.get('Content-Length', 0))
            if content_length <= 0:
                return self._json({'error': 'empty body'}, 400)
            body = self.rfile.read(content_length)
            data = json.loads(body)

            url_template = data.get('urlTemplate', '')
            tiles = data.get('tiles', [])
            subdomains = data.get('subdomains', ['a', 'b', 'c', 'd'])
            max_workers = data.get('workers', 16)

            if not url_template or not tiles:
                return self._json({'error': 'missing urlTemplate or tiles'}, 400)

            print(f'[tiles] 开始下载 {len(tiles)} 张瓦片，{max_workers} 线程并发', flush=True)

            results = {}  # key -> (success, data)
            lock = threading.Lock()
            download_count = [0]  # mutable counter
            total = len(tiles)

            def record_success(key, img_data):
                with lock:
                    results[key] = (True, img_data)
                    download_count[0] += 1
                    if download_count[0] % 50 == 0:
                        print(f'[tiles] 已下载 {download_count[0]}/{total}', flush=True)

            def fetch_tile(url):
                req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    if resp.status != 200:
                        return None
                    img_data = resp.read()
                    return img_data if len(img_data) > 100 else None

            def download_one(tile):
                x, y, z = tile['x'], tile['y'], tile['z']
                key = f'{z}/{x}/{y}'
                base_hash = hash(key)

                # 最多尝试 len(subdomains) 个不同子域名
                for attempt in range(len(subdomains)):
                    sub = subdomains[(base_hash + attempt) % len(subdomains)]
                    url = (url_template
                           .replace('{s}', sub)
                           .replace('{x}', str(x))
                           .replace('{y}', str(y))
                           .replace('{z}', str(z)))
                    try:
                        img_data = fetch_tile(url)
                        if img_data is not None:
                            record_success(key, img_data)
                            return
                    except Exception:
                        pass

                with lock:
                    results[key] = (False, None)

            # 多线程下载
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(download_one, tile) for tile in tiles]
                for f in as_completed(futures):
                    try:
                        f.result()
                    except Exception:
                        pass

            # 打包为 ZIP
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
                success_count = 0
                for key, (ok, data) in results.items():
                    if ok:
                        zf.writestr(f'tiles/{key}.png', data)
                        success_count += 1
                # 写入元数据
                zf.writestr('metadata.json', json.dumps({
                    'totalRequested': len(tiles),
                    'totalDownloaded': success_count,
                    'failed': len(tiles) - success_count,
                    'createdAt': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                }, indent=2))

            print(f'[tiles] 完成: {success_count}/{len(tiles)} 张瓦片下载成功', flush=True)

            zip_data = buf.getvalue()
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Disposition', 'attachment; filename="tiles.zip"')
            self.send_header('Content-Length', str(len(zip_data)))
            self._cors_headers()
            self.end_headers()
            self.wfile.write(zip_data)

        except Exception as e:
            traceback.print_exc()
            self._json({'error': str(e)}, 500)

    # ========== 静态文件 ==========

    def _serve_static(self, path):
        if path == '/' or path == '':
            path = '/index.html'

        # 安全检查：解析真实路径，确保仍在 STATIC_DIR 内
        file_path = os.path.realpath(os.path.join(STATIC_DIR, path.lstrip('/')))
        if not file_path.startswith(STATIC_DIR + os.sep) or not os.path.isfile(file_path):
            return self._json({'error': 'not found'}, 404)

        content_type, _ = mimetypes.guess_type(file_path)
        if content_type is None:
            content_type = 'application/octet-stream'

        try:
            with open(file_path, 'rb') as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._json({'error': str(e)}, 500)

    # ========== 工具方法 ==========

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors_headers()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, status, body, content_type):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self._cors_headers()
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')


def main():
    port = int(os.environ.get('PORT', '8080'))
    # 异步预加载 Whisper 模型
    threading.Thread(target=get_model, daemon=True).start()
    print(f'[server] 启动 http://0.0.0.0:{port}', flush=True)
    print(f'[server] 静态文件目录: {STATIC_DIR}', flush=True)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    srv.serve_forever()


if __name__ == '__main__':
    main()
