import requests
headers = {'User-Agent': 'Mozilla/5.0','Origin': 'https://www.cse.lk','Referer': 'https://www.cse.lk/'}
s = requests.Session()
s.get('https://www.cse.lk/', headers=headers)
r = s.post('https://www.cse.lk/api/mostActiveTrades', data={}, headers=headers, timeout=10)
import json
print(json.dumps(r.json()[0], indent=2))
