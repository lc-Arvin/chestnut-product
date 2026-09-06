"""Exercise HTTP startup with the files included by the current Docker context."""
import fnmatch
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CloudPackagingTests(unittest.TestCase):
    def test_http_startup_without_miniprogram(self):
        # Current .dockerignore uses simple excluded names/globs. Fail explicitly
        # if richer rules are added rather than silently simulating them wrong.
        patterns = [line.strip() for line in (ROOT / '.dockerignore').read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
        self.assertTrue(all(not p.startswith('!') and '/' not in p and '**' not in p
                            for p in patterns), 'Update Docker context simulation for new ignore syntax')
        files = subprocess.check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
            cwd=ROOT, text=True,
        ).splitlines()
        with tempfile.TemporaryDirectory() as directory:
            context = Path(directory)
            for name in set(files):
                relative = Path(name)
                if any(fnmatch.fnmatch(part, pattern) for part in relative.parts for pattern in patterns):
                    continue
                source = ROOT / relative
                if not source.is_file():
                    continue
                target = context / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            self.assertFalse((context / 'miniprogram').exists())
            self.assertTrue((context / 'shared/languages.json').is_file())
            # A fresh process imports the packaged server, binds HTTP and serves
            # the health check and language catalog, without using the source tree.
            script = '''
import asyncio
from aiohttp.test_utils import TestClient, TestServer
import server

async def verify():
    async with TestClient(TestServer(server.create_app())) as client:
        response = await client.get('/health')
        assert response.status == 200
        assert (await response.json())['status'] == 'ok'
        response = await client.get('/api/languages')
        assert response.status == 200
        labels = await response.json()
        assert labels['zh'] == '中文（简体）' and labels['en'] == 'English'
        assert labels['ja'] == '日本語' and labels['yue'] == '粤语'
        assert server.parse_language_pair() == ('zh', 'en')
        assert server.parse_language_pair('ja,fr') == ('ja', 'fr')
asyncio.run(verify())
'''
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('CHESTNUT_', 'DASHSCOPE_', 'BAILIAN_', 'TENCENTCLOUD_'))
                   and key != 'PYTHONPATH'}
            result = subprocess.run([sys.executable, '-c', script], cwd=context,
                                    env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
