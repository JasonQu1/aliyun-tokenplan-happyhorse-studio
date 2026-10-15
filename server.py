"""Local HappyHorse Studio. Python 3.9+, standard library only."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
WEB = ROOT / 'web'
REGIONS = {'cn-beijing', 'ap-southeast-1', 'us-east-1', 'eu-central-1', 'ap-northeast-1'}
RATIOS = {'16:9', '9:16', '1:1', '4:3', '3:4', '4:5', '5:4', '9:21', '21:9'}
TASK_PATTERN = re.compile(r'^[A-Za-z0-9_-]{1,128}$')
TOKEN = secrets.token_urlsafe(32)
RESULTS = {}
LOCK = threading.Lock()
DOWNLOADS = Path(__file__).resolve().parent / 'downloads'


def public_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('素材必须是 HTTP/HTTPS 公网链接。')
    return value


def integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name}必须是 {low}～{high} 之间的整数。')
    return value


def image_url(value):
    if not isinstance(value, str):
        raise ValueError('图片地址格式错误。')
    if value.startswith('data:'):
        match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)', value)
        if not match:
            raise ValueError('图片仅支持 JPEG、PNG、WEBP。')
        try:
            data = base64.b64decode(match[2], validate=True)
        except ValueError:
            raise ValueError('图片 Base64 数据无效。')
        if not data or len(data) > 20 * 1024 * 1024:
            raise ValueError('每张图片不能超过 20 MB。')
        magic = (data.startswith(b'\xff\xd8\xff') if match[1] == 'jpeg' else
                 data.startswith(b'\x89PNG\r\n\x1a\n') if match[1] == 'png' else
                 data.startswith(b'RIFF') and data[8:12] == b'WEBP')
        if not magic:
            raise ValueError('图片内容与所选格式不一致。')
        return value
    return public_url(value)


def build_payload(data):
    mode = data.get('mode')
    if mode not in ('t2v', 'i2v', 'r2v', 'edit'):
        raise ValueError('请选择有效模式。')
    version = data.get('version', '1.1')
    if version not in ('1.0', '1.1'):
        raise ValueError('模型版本无效。')
    prompt = data.get('prompt', '').strip()
    if not prompt and mode != 'i2v':
        raise ValueError('请输入提示词。')
    chinese = sum('\u4e00' <= ch <= '\u9fff' for ch in prompt)
    if chinese > 2500 or len(prompt) - chinese > 5000:
        raise ValueError('提示词超过 2500 个中文字符或 5000 个非中文字符。')
    images = data.get('images', [])
    if not isinstance(images, list):
        raise ValueError('图片列表格式错误。')
    required = {'t2v': (0, 0), 'i2v': (1, 1), 'r2v': (1, 9), 'edit': (0, 5)}[mode]
    if not required[0] <= len(images) <= required[1]:
        raise ValueError(f'此模式需要 {required[0]}～{required[1]} 张图片。')
    inputs = {'prompt': prompt}
    media = [{'type': 'first_frame' if mode == 'i2v' else 'reference_image', 'url': image_url(url)} for url in images]
    if mode == 'edit':
        media.insert(0, {'type': 'video', 'url': public_url(data.get('video_url', ''))})
    if media:
        inputs['media'] = media
    resolution = data.get('resolution', '720P')
    if resolution not in ({'720P', '1080P'} if mode == 'edit' else {'480P', '720P', '1080P'}):
        raise ValueError('此模式不支持所选分辨率。')
    watermark = data.get('watermark', True)
    if not isinstance(watermark, bool):
        raise ValueError('水印参数必须为布尔值。')
    parameters = {'resolution': resolution, 'watermark': watermark}
    if mode != 'edit':
        parameters['duration'] = integer(data.get('duration', 5), 3, 15, '时长')
    if mode in ('t2v', 'r2v'):
        ratio = data.get('ratio', '16:9')
        if ratio not in RATIOS:
            raise ValueError('宽高比无效。')
        parameters['ratio'] = ratio
    if mode == 'edit':
        audio = data.get('audio_setting', 'auto')
        if audio not in ('auto', 'origin'):
            raise ValueError('声音选项无效。')
        parameters['audio_setting'] = audio
    if data.get('seed') is not None:
        parameters['seed'] = integer(data['seed'], 0, 2147483647, '随机种子')
    model = 'happyhorse-1.0-video-edit' if mode == 'edit' else f'happyhorse-{version}-{mode}'
    return {'model': model, 'input': inputs, 'parameters': parameters}


def endpoint(data):
    billing = data.get('billing', 'auto')
    key = data.get('api_key', '').strip()
    if billing not in ('auto', 'payg', 'tokenplan'):
        raise ValueError('接入方式无效。')
    if billing == 'auto':
        billing = 'tokenplan' if key.startswith('sk-sp-') else 'payg'
    if billing == 'tokenplan':
        if not key.startswith('sk-sp-'):
            raise ValueError('TokenPlan 接入需要“我的订阅”中的 sk-sp- 专用 Key。')
        return 'https://token-plan.ap-southeast-1.maas.aliyuncs.com'
    if key.startswith('sk-sp-'):
        raise ValueError('sk-sp- 是套餐专用 Key，请选择 TokenPlan 接入，不能使用按量付费接口。')
    region = data.get('region', '')
    workspace = data.get('workspace', '').strip()
    if region not in REGIONS:
        raise ValueError('地域无效。')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,127}', workspace):
        raise ValueError('请输入有效的业务空间 ID。')
    if region == 'us-east-1' and data.get('mode') == 'edit':
        return 'https://dashscope-us.aliyuncs.com'
    return f'https://{workspace}.{region}.maas.aliyuncs.com'


def cloud_request(data, task=None):
    base = endpoint(data)
    key = data.get('api_key', '').strip()
    if not key or '\n' in key or '\r' in key:
        raise ValueError('请填写 API Key。')
    headers = {'Authorization': 'Bearer ' + key}
    if task:
        if not TASK_PATTERN.fullmatch(task):
            raise ValueError('任务 ID 无效。')
        url = base + '/api/v1/tasks/' + task
        body = None
    else:
        url = base + '/api/v1/services/aigc/video-generation/video-synthesis'
        body = json.dumps(build_payload(data), ensure_ascii=False).encode('utf-8')
        headers.update({'Content-Type': 'application/json', 'X-DashScope-Async': 'enable'})
    request = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        try:
            info = json.load(error)
            message = str(info.get('code', error.code)) + ': ' + str(info.get('message', '调用失败'))
        except (ValueError, AttributeError):
            message = f'云端请求失败：HTTP {error.code}'
        raise ValueError(message.replace(key, '[已隐藏]'))
    except (TimeoutError, urllib.error.URLError) as error:
        detail = str(error.reason) if isinstance(error, urllib.error.URLError) else '请求超时'
        if isinstance(getattr(error, 'reason', None), ssl.SSLCertVerificationError):
            detail = '证书验证失败。请运行 Python 安装目录中的 Install Certificates.command。'
        if task:
            raise ValueError('查询失败，可用原任务 ID 继续查询。' + detail.replace(key, '[已隐藏]'))
        raise ValueError('提交响应未收到，任务可能已创建。请先在百炼控制台检查，避免重复计费。' + detail.replace(key, '[已隐藏]'))
    if not isinstance(result, dict):
        raise ValueError('云端返回格式错误。')
    if result.get('code'):
        raise ValueError((str(result['code']) + ': ' + str(result.get('message', '调用失败'))).replace(key, '[已隐藏]'))
    output = result.get('output', {})
    if output.get('task_status') == 'SUCCEEDED' and output.get('video_url'):
        task_id = output.get('task_id', task)
        if task_id and TASK_PATTERN.fullmatch(task_id):
            with LOCK:
                RESULTS[task_id] = output['video_url']
    return result


def save_video(task):
    if not isinstance(task, str) or not TASK_PATTERN.fullmatch(task):
        raise ValueError('任务 ID 无效。')
    with LOCK:
        url = RESULTS.get(task)
    if not url:
        raise ValueError('请先查询成功任务，再下载视频。')
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or not parsed.hostname.endswith('.aliyuncs.com'):
        raise ValueError('视频链接域名无法自动下载，请使用云端原始链接保存。')
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target = DOWNLOADS / (task + '.mp4')
    if not target.exists():
        temporary = DOWNLOADS / (task + '.' + secrets.token_hex(4) + '.part')
        try:
            with urllib.request.urlopen(url, timeout=120) as source, temporary.open('wb') as destination:
                total = 0
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > 1024 * 1024 * 1024:
                        raise ValueError('视频文件超过 1 GB。')
                    destination.write(chunk)
                if not total:
                    raise ValueError('视频文件为空。')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return {'local_url': '/videos/' + target.name, 'path': str(target)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def trusted_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def send(self, status, body, mime='application/json; charset=utf-8'):
        if isinstance(body, dict):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob: https: http:; media-src 'self' https: http: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.trusted_host():
            return self.send(403, {'error': '只接受本地访问。'})
        path = urllib.parse.urlsplit(self.path).path
        if path == '/health':
            return self.send(200, {'app': 'happyhorse-studio', 'version': '2026.10.13'})
        if path == '/':
            body = (WEB / 'index.html').read_text('utf-8').replace('__APP_TOKEN__', TOKEN).encode('utf-8')
            return self.send(200, body, 'text/html; charset=utf-8')
        if path in ('/app.js', '/style.css'):
            return self.send(200, (WEB / path[1:]).read_bytes(), 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
        if path.startswith('/videos/'):
            name = path[len('/videos/'):]
            if not name.endswith('.mp4') or not TASK_PATTERN.fullmatch(name[:-4]):
                return self.send(404, {'error': '文件不存在。'})
            file = DOWNLOADS / name
            if not file.is_file():
                return self.send(404, {'error': '文件不存在。'})
            size = file.stat().st_size
            start, end = 0, size - 1
            range_header = self.headers.get('Range')
            if range_header:
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', range_header)
                if not match or not any(match.groups()):
                    return self.send(416, {'error': 'Range 无效。'})
                if match[1]:
                    start = int(match[1])
                    end = min(int(match[2]), end) if match[2] else end
                else:
                    start = max(0, size - int(match[2]))
                if start > end or start >= size:
                    return self.send(416, {'error': 'Range 超出范围。'})
            self.send_response(206 if range_header else 200)
            self.send_header('Content-Type', 'video/mp4')
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(end - start + 1))
            if range_header:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.end_headers()
            try:
                with file.open('rb') as source:
                    source.seek(start)
                    remaining = end - start + 1
                    while remaining:
                        chunk = source.read(min(1024 * 1024, remaining))
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send(404, {'error': '页面不存在。'})

    def do_POST(self):
        origin = self.headers.get('Origin')
        if not self.trusted_host() or self.headers.get('X-App-Token') != TOKEN or (origin and origin != f'http://127.0.0.1:{self.server.server_port}'):
            return self.send(403, {'error': '本地访问校验失败，请刷新页面。'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 260 * 1024 * 1024:
                raise ValueError('请求为空或超过 260 MB。')
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('请求格式错误。')
            if self.path == '/api/create':
                result = cloud_request(data)
            elif self.path == '/api/query':
                task = data.get('task_id', '')
                if not isinstance(task, str) or not TASK_PATTERN.fullmatch(task):
                    raise ValueError('任务 ID 无效。')
                result = cloud_request(data, task=task)
            elif self.path == '/api/download':
                result = save_video(data.get('task_id', ''))
            elif self.path == '/api/shutdown':
                self.send(200, {'message': '应用已关闭，云端任务仍可用任务 ID 查询。'})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            else:
                return self.send(404, {'error': '接口不存在。'})
            self.send(200, result)
        except (ValueError, TypeError, AttributeError, urllib.error.URLError, OSError) as error:
            self.send(400, {'error': str(error).replace(data.get('api_key', ''), '[已隐藏]') if isinstance(locals().get('data'), dict) and data.get('api_key') else str(error)})


def main():
    global DOWNLOADS
    parser = argparse.ArgumentParser(description='HappyHorse Studio 本地可视化工具')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.output:
        DOWNLOADS = args.output.resolve()
    elif getattr(sys, 'frozen', False):
        DOWNLOADS = Path.home() / 'Videos' / 'HappyHorse'
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        if not getattr(sys, 'frozen', False):
            raise
        existing_url = f'http://127.0.0.1:{args.port}'
        try:
            with urllib.request.urlopen(existing_url + '/health', timeout=2) as response:
                existing = json.load(response)
            if existing.get('app') == 'happyhorse-studio' and existing.get('version') == '2026.10.13':
                if not args.no_browser:
                    webbrowser.open(existing_url)
                return
        except (OSError, ValueError):
            pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    url = f'http://127.0.0.1:{server.server_port}'
    if sys.stdout is not None and not getattr(sys, 'frozen', False):
        print(f'HappyHorse Studio: {url}\n视频保存目录: {DOWNLOADS}\n关闭终端或按 Ctrl+C 停止服务。', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        if getattr(sys, 'frozen', False) and sys.platform == 'win32':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '启动失败：' + str(error), 'HappyHorse Studio', 0x10)
        else:
            raise
