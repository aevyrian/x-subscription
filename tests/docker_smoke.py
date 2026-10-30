"""Exercise a released container with a disposable volume and no X credentials."""
import http.cookiejar
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, timeout=120).strip()


def main(image, platform):
    name = 'xarchive-smoke-' + uuid.uuid4().hex
    volume = name + '-data'
    password = uuid.uuid4().hex
    client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = ''

    def request(path, body=None, expected=200):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data,
                                     headers={'Content-Type': 'application/json', 'Origin': base})
        try:
            response = client.open(req, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            assert response.status == expected, (path, response.status, expected)
            return json.load(response)

    def ready():
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                result = request('/health')
                if result['status'] == 'ok' and result['worker']:
                    return
            except (OSError, AssertionError, ValueError):
                pass
            time.sleep(1)
        raise AssertionError('Container did not become healthy')

    docker('volume', 'create', volume)
    try:
        docker('pull', '--platform', platform, image)
        docker('run', '-d', '--platform', platform, '--name', name, '--init', '-p', '127.0.0.1::8787',
               '-v', volume + ':/data', image)
        base = 'http://' + docker('port', name, '8787/tcp')
        ready()
        assert docker('exec', name, 'id', '-u') != '0'
        assert request('/api/v1/auth')['setup_required']
        request('/api/v1/settings', expected=401)
        request('/api/v1/auth/setup', {'password': password})
        assert request('/api/v1/auth')['logged_in']
        request('/api/v1/settings')
        docker('restart', '--timeout', '30', name)
        base = 'http://' + docker('port', name, '8787/tcp')
        ready()
        client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        assert not request('/api/v1/auth')['setup_required']
        request('/api/v1/auth/login', {'password': password})
        assert request('/api/v1/auth')['logged_in']
        request('/api/v1/settings')
        print('Container startup, authentication, non-root user and restart persistence passed:', image)
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=False, stdout=subprocess.DEVNULL, timeout=60)
        subprocess.run(['docker', 'volume', 'rm', volume], check=False, stdout=subprocess.DEVNULL, timeout=60)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
