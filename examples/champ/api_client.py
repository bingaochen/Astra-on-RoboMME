"""Direct Responses API; the credential is read once and never logged."""
import base64
import json
import mimetypes
import random
from email.utils import parsedate_to_datetime
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from core import atomic_json


class ResponsesClient:
    def __init__(self, key):
        self.next_request_at = 0.0
        self.key = key.strip().replace('\\_', '_')
        if not self.key.startswith('sk-'):
            raise ValueError('Missing API credential')

    @classmethod
    def from_key_file(cls, path):
        path = Path(path)
        key = path.read_text()
        return cls(key)

    def scrub(self, text):
        return re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text.replace(self.key, '[REDACTED]'))

    def _send(self, request, out):
        # Scheduling only: identical request, paused simulator, bounded 429 retries.
        for attempt in range(9):
            for parent in out.parents:
                if parent.name in ('group_0', 'group_1') and (parent/'STOP.json').exists():
                    raise RuntimeError('Host requested stop; no new API request')
            time.sleep(max(0, self.next_request_at-time.monotonic()))
            self.next_request_at = time.monotonic()+20
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.loads(response.read()), response.headers.get('x-request-id')
            except urllib.error.HTTPError as error:
                body = error.read().decode('utf-8', errors='replace')
                try:
                    detail = json.loads(body).get('error', {})
                except ValueError:
                    detail = {}
                code = detail.get('code') if isinstance(detail, dict) else None
                kind = detail.get('type') if isinstance(detail, dict) else None
                headers = {k:v for k,v in error.headers.items()
                           if k.lower() == 'retry-after' or k.lower() == 'x-request-id'
                           or k.lower().startswith('x-ratelimit-')}
                atomic_json(out/f'http_error_{attempt:02d}.json',
                            json.loads(self.scrub(json.dumps({'status':error.code, 'body':body,
                            'headers':headers, 'time':time.time()}))))
                retryable = error.code == 429 and (code in ('rate_limit_exceeded', 'slow_down')
                              or kind == 'rate_limit_error')
                if not retryable or attempt == 8:
                    raise RuntimeError(self.scrub(f'HTTP {error.code}: {body}')) from None
                delay = min(120, 10*2**attempt)
                retry_after = error.headers.get('Retry-After')
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        try:
                            delay = max(delay, parsedate_to_datetime(retry_after).timestamp()-time.time())
                        except (TypeError, ValueError, OverflowError):
                            pass
                time.sleep(delay+random.uniform(0, 3))

    def __call__(self, out):
        out = Path(out)
        if (out/'response.json').exists():
            return
        if (out/'api_started.json').exists():
            raise RuntimeError('Planner API previous request outcome uncertain; no automatic paid retry')
        req = json.loads((out/'request.json').read_text())
        content = [{'type': 'input_text', 'text': (out/'prompt.txt').read_text()}]
        for name in req['images']:
            if Path(name).name != name:
                raise ValueError('Invalid attachment path')
            image = out/name
            mime = mimetypes.guess_type(name)[0]
            content.append({'type': 'input_image', 'detail': 'high',
                            'image_url': 'data:'+mime+';base64,'+base64.b64encode(image.read_bytes()).decode()})
        payload = {'model': 'gpt-6-astra', 'reasoning': {'effort': 'medium'},
                   'input': [{'role': 'user', 'content': content}],
                   'max_output_tokens': 2048, 'store': False}
        atomic_json(out/'api_started.json', {'time': time.time(), 'model': payload['model'],
                                           'reasoning': payload['reasoning'], 'max_output_tokens': 2048,
                                           'store': False, 'image_count': len(req['images'])})
        request = urllib.request.Request('https://api.openai.com/v1/responses',
                    data=json.dumps(payload).encode(),
                    headers={'Authorization': 'Bearer '+self.key, 'Content-Type': 'application/json'})
        started = time.monotonic()
        try:
            data, request_id = self._send(request, out)
            atomic_json(out/'api_response.json', json.loads(self.scrub(json.dumps(data))))
            answer = '\n'.join(c.get('text', '') for o in data.get('output', [])
                               if o.get('type') == 'message' for c in o.get('content', [])
                               if c.get('type') == 'output_text')
            ok = (data.get('status') == 'completed'
                  and data.get('model', '').startswith('gpt-6-astra')
                  and data.get('reasoning', {}).get('effort') == 'medium')
            result = {'status': 'ok' if ok else 'error', 'text': answer,
                      'model': data.get('model'), 'effort': 'medium',
                      'response_status': data.get('status'), 'usage': data.get('usage'),
                      'request_id': request_id, 'seconds': time.monotonic()-started}
        except Exception as error:
            result = {'status': 'error', 'error': self.scrub(str(error)),
                      'seconds': time.monotonic()-started}
        atomic_json(out/'response.json', json.loads(self.scrub(json.dumps(result))))
