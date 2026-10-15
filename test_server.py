import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.error
import urllib.request

import server


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class ApiTests(unittest.TestCase):
    def config(self, mode='t2v', **extra):
        data = dict(mode=mode, prompt='测试', region='cn-beijing', workspace='ws-123', api_key='secret-key', images=[], resolution='720P', duration=5, ratio='9:16', watermark=False)
        data.update(extra)
        return data

    def test_four_real_request_contracts(self):
        cases = [
            ('t2v', [], {}, 'happyhorse-1.1-t2v', [], {'resolution', 'watermark', 'duration', 'ratio'}),
            ('i2v', ['https://example.com/a.png'], {'prompt': ''}, 'happyhorse-1.1-i2v', ['first_frame'], {'resolution', 'watermark', 'duration'}),
            ('r2v', ['https://example.com/a.png', 'https://example.com/b.png'], {}, 'happyhorse-1.1-r2v', ['reference_image', 'reference_image'], {'resolution', 'watermark', 'duration', 'ratio'}),
            ('edit', ['https://example.com/a.png'], {'video_url': 'https://example.com/a.mp4', 'audio_setting': 'origin'}, 'happyhorse-1.0-video-edit', ['video', 'reference_image'], {'resolution', 'watermark', 'audio_setting'}),
        ]
        for mode, images, extra, model, types, fields in cases:
            with self.subTest(mode=mode):
                response = {'output': {'task_id': 'task-123', 'task_status': 'PENDING'}}
                with patch('server.urllib.request.urlopen', return_value=Response(json.dumps(response).encode())) as call:
                    result = server.cloud_request(self.config(mode, images=images, **extra))
                    request = call.call_args[0][0]
                    payload = json.loads(request.data)
                    self.assertEqual(request.get_header('X-dashscope-async'), 'enable')
                    self.assertEqual(request.get_header('Authorization'), 'Bearer secret-key')
                    self.assertEqual(payload['model'], model)
                    self.assertEqual(set(payload['parameters']), fields)
                    self.assertEqual([m['type'] for m in payload['input'].get('media', [])], types)
                    self.assertEqual(result, response)

    def test_limits_and_input_validation(self):
        for data in [self.config('i2v'), self.config('r2v'), self.config('r2v', images=['https://x.com/a']*10), self.config('edit',video_url='file:///tmp/a.mp4'), self.config('edit',video_url='https://x.com/a',resolution='480P'), self.config(duration=2), self.config(seed=-1), self.config(seed=1.5), self.config(prompt='中'*2501), self.config('i2v', images=['data:image/png;base64,YQ=='])]:
            with self.subTest(data={k:v for k,v in data.items() if k not in ('api_key','prompt')}):
                with self.assertRaises(ValueError):
                    server.build_payload(data)
        self.assertEqual(server.endpoint(self.config('edit', region='us-east-1')), 'https://dashscope-us.aliyuncs.com')
        with self.assertRaises(ValueError):
            server.endpoint(self.config(workspace='evil.com/path'))

    def test_query_get_and_success_url(self):
        response = {'output': {'task_id':'task-123','task_status':'SUCCEEDED','video_url':'https://result.oss-cn-beijing.aliyuncs.com/video.mp4'}}
        with patch('server.urllib.request.urlopen', return_value=Response(json.dumps(response).encode())) as call:
            server.cloud_request(self.config(), task='task-123')
            request = call.call_args[0][0]
            self.assertIsNone(request.data)
            self.assertTrue(request.full_url.endswith('/api/v1/tasks/task-123'))
            self.assertEqual(server.RESULTS['task-123'], response['output']['video_url'])

    def test_token_plan_creation_and_poll_use_dedicated_host(self):
        config = self.config(api_key='sk-sp-test', workspace='', region='cn-beijing')
        for task in (None, 'task-123'):
            with self.subTest(task=task):
                with patch('server.urllib.request.urlopen', return_value=Response(b'{"output":{"task_id":"task-123","task_status":"PENDING"}}')) as call:
                    server.cloud_request(config, task=task)
                    request = call.call_args[0][0]
                    expected = '/api/v1/tasks/task-123' if task else '/api/v1/services/aigc/video-generation/video-synthesis'
                    self.assertEqual(request.full_url, 'https://token-plan.ap-southeast-1.maas.aliyuncs.com' + expected)
                    self.assertEqual(request.get_header('Authorization'), 'Bearer sk-sp-test')

    def test_billing_modes_cannot_mix_keys(self):
        with self.assertRaisesRegex(ValueError, 'TokenPlan'):
            server.cloud_request(self.config(api_key='sk-sp-test', billing='payg'))
        with self.assertRaisesRegex(ValueError, 'sk-sp-'):
            server.cloud_request(self.config(api_key='sk-test', billing='tokenplan'))
        with self.assertRaisesRegex(ValueError, '接入方式'):
            server.endpoint(self.config(billing='unknown'))

    def test_uncertain_submission_never_retries(self):
        with patch('server.urllib.request.urlopen', side_effect=urllib.error.URLError('timeout')) as call:
            with self.assertRaisesRegex(ValueError, '任务可能已创建'):
                server.cloud_request(self.config())
            self.assertEqual(call.call_count, 1)

    def test_cloud_error_does_not_expose_key(self):
        error=urllib.error.HTTPError('https://x.com', 401, 'Unauthorized', {}, Response(b'{"code":"InvalidApiKey","message":"secret-key is bad"}'))
        with patch('server.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, r'\[已隐藏\]') as result:
                server.cloud_request(self.config())
            self.assertNotIn('secret-key', str(result.exception))


class LocalHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.worker.join()

    def post(self, path, data, token=server.TOKEN):
        return urllib.request.urlopen(urllib.request.Request(self.base+path, data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-App-Token':token}), timeout=3)

    def test_page_and_csrf(self):
        with urllib.request.urlopen(self.base) as response:
            self.assertIn(server.TOKEN, response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as result:
            self.post('/api/create',{},token='wrong')
        self.assertEqual(result.exception.code,403)

    def test_query_failure_and_download_range(self):
        with patch('server.cloud_request',return_value={'output':{'task_status':'FAILED','code':'InvalidParameter','message':'bad input'}}):
            with self.post('/api/query', {'task_id':'task-123'}) as response:
                self.assertEqual(json.load(response)['output']['task_status'],'FAILED')
        with tempfile.TemporaryDirectory() as temporary, patch('server.DOWNLOADS',Path(temporary)):
            server.RESULTS['download-123']='https://result.oss-cn-beijing.aliyuncs.com/v.mp4'
            with patch('server.urllib.request.urlopen', return_value=Response(b'fake-mp4-content')):
                result=server.save_video('download-123')
            self.assertEqual(Path(result['path']).read_bytes(), b'fake-mp4-content')
            request=urllib.request.Request(self.base+result['local_url'],headers={'Range':'bytes=0-3'})
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status,206)
                self.assertEqual(response.read(),b'fake')
            with self.assertRaises(ValueError):
                server.save_video('../escape')
            server.RESULTS['bad-host']='https://evil.test/v.mp4'
            with self.assertRaises(ValueError):
                server.save_video('bad-host')


if __name__ == '__main__':
    unittest.main()
