import io,json,tempfile,unittest,urllib.error
from pathlib import Path
from unittest.mock import patch,MagicMock
from api_client import ResponsesClient

class RetryTests(unittest.TestCase):
 def error(self,code):
  return urllib.error.HTTPError('https://api.openai.com',429,'Too Many Requests',{'Retry-After':'45'},io.BytesIO(json.dumps({'error':{'code':code}}).encode()))
 def test_rate_limit_waits_then_same_request_succeeds(self):
  client=ResponsesClient('sk-test');request=object();response=MagicMock();response.__enter__.return_value=response;response.read.return_value=b'{"status":"completed"}';response.headers={'x-request-id':'test'}
  with tempfile.TemporaryDirectory() as tmp,patch('api_client.urllib.request.urlopen',side_effect=[self.error('rate_limit_exceeded'),response]) as send,patch('api_client.time.sleep') as sleep:
   data,rid=client._send(request,Path(tmp));self.assertEqual(rid,'test');self.assertEqual(send.call_count,2);self.assertTrue(all(c.args[0] is request for c in send.call_args_list));self.assertTrue(any(c.args[0]>=45 for c in sleep.call_args_list));self.assertEqual(json.loads((Path(tmp)/'http_error_00.json').read_text())['status'],429)
 def test_quota_does_not_retry(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_client.urllib.request.urlopen',side_effect=self.error('credit_balance_exhausted')) as send,patch('api_client.time.sleep'):
   with self.assertRaisesRegex(RuntimeError,'credit_balance_exhausted'):ResponsesClient('sk-test')._send(object(),Path(tmp))
   self.assertEqual(send.call_count,1)
 def test_retries_are_bounded(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_client.urllib.request.urlopen',side_effect=[self.error('slow_down') for _ in range(9)]) as send,patch('api_client.time.sleep'):
   with self.assertRaisesRegex(RuntimeError,'slow_down'):ResponsesClient('sk-test')._send(object(),Path(tmp))
   self.assertEqual(send.call_count,9)
 def test_host_stop_prevents_request(self):
  with tempfile.TemporaryDirectory() as tmp,patch('api_client.urllib.request.urlopen') as send:
   group=Path(tmp)/'group_0';out=group/'shard_02'/'planner_calls'/'request';out.mkdir(parents=True)
   (group/'STOP.json').write_text('{}')
   with self.assertRaisesRegex(RuntimeError,'Host requested stop'):
    ResponsesClient('sk-test')._send(object(),out)
   send.assert_not_called()
if __name__=='__main__':unittest.main()
