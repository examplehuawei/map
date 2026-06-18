"""
本地语音识别后端：接收浏览器上传的音频，用 faster-whisper 识别中文，返回文本
"""
import os
import sys
import json
import time
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

# 延迟加载模型（启动快）
_model_lock = threading.Lock()
_model = None

def get_model():
    global _model
    with _model_lock:
        if _model is None:
            print('[whisper] 正在加载模型（首次约 1-2 分钟，之后秒级）...', flush=True)
            from faster_whisper import WhisperModel
            # base 模型：~75MB，中文识别可用；tiny ~40MB 更快但精度低
            # 首次运行会自动从 Hugging Face 下载模型
            _model = WhisperModel('base', device='cpu', compute_type='int8')
            print('[whisper] 模型加载完成', flush=True)
        return _model


def transcribe(audio_path, language='zh'):
    model = get_model()
    segments, info = model.transcribe(
        audio_path,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500)
    )
    text_parts = []
    for seg in segments:
        text_parts.append(seg.text)
    return ''.join(text_parts).strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # 静默默认日志
        sys.stderr.write('[%s] %s\n' % (self.address_string(), fmt % args))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/health':
            self._json({'ok': True, 'model_loaded': _model is not None})
        else:
            self._json({'error': 'not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/asr':
            self._handle_asr()
        else:
            self._json({'error': 'not found'}, 404)

    def _handle_asr(self):
        try:
            content_type = self.headers.get('Content-Type', '')
            content_length = int(self.headers.get('Content-Length', '0'))
            if content_length <= 0:
                return self._json({'error': 'empty body'}, 400)

            raw = self.rfile.read(content_length)

            # 保存为临时文件
            suffix = '.webm'
            if 'ogg' in content_type:
                suffix = '.ogg'
            elif 'wav' in content_type:
                suffix = '.wav'
            elif 'mp4' in content_type or 'm4a' in content_type:
                suffix = '.m4a'

            tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(tmp_fd, 'wb') as f:
                f.write(raw)
            print(f'[asr] 收到 {len(raw)} 字节音频，保存到 {tmp_path}', flush=True)

            try:
                t0 = time.time()
                text = transcribe(tmp_path, language='zh')
                dt = time.time() - t0
                print(f'[asr] 识别完成: {text!r}  耗时 {dt:.2f}s', flush=True)
                self._json({'text': text, 'time': round(dt, 2)})
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._json({'error': str(e)}, 500)

    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    port = int(os.environ.get('ASR_PORT', '8081'))
    # 预加载模型（异步，不阻塞服务器启动）
    threading.Thread(target=get_model, daemon=True).start()
    print(f'[asr-server] 启动 http://0.0.0.0:{port}', flush=True)
    srv = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    srv.serve_forever()


if __name__ == '__main__':
    main()
