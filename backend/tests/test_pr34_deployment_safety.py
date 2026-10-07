"""Execute deployment failure branches only inside a temporary filesystem."""
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('failure', ['home', 'asset', 'connection', 'none'])
def test_frontend_swap_restores_bytes_and_both_markers(tmp_path, failure):
    src = tmp_path / 'src'
    (src / 'assets').mkdir(parents=True)
    (src / 'index.html').write_text('<script src="/assets/index-fixture.js"></script>')
    (src / 'assets' / 'index-fixture.js').write_text('synthetic')
    (src / 'icon-shield.png').write_bytes(b'fixture')
    webroot = tmp_path / 'webroot'
    webroot.mkdir()
    (webroot / 'index.html').write_text('old homepage')
    marker = tmp_path / 'current-release'
    metadata = tmp_path / 'current-release.json'
    marker.write_text('old-sha\n')
    metadata.write_text('{"release":"old-sha"}\n')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    stubs = {
        'chown': '#!/bin/sh\nexit 0\n',
        'curl': '''#!/bin/sh
for arg in "$@"; do
  case "$arg" in
    http://*) url="$arg" ;;
  esac
done
if [ "$PROBE_FAIL" = connection ]; then exit 7; fi
if [ "$PROBE_FAIL" = home ] && [ "$url" = 'http://127.0.0.1:8080/' ]; then printf 503; exit 0; fi
if [ "$PROBE_FAIL" = asset ] && echo "$url" | grep -q /assets/; then printf 404; exit 0; fi
case "$url" in */favicon.ico) printf 404 ;; *) printf 200 ;; esac
''',
    }
    for name, script in stubs.items():
        p = bin_dir / name
        p.write_text(script)
        p.chmod(0o700)
    env = os.environ | {
        'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
        'PAPERGUARD_WEBROOT': str(webroot), 'PAPERGUARD_RELEASES': str(tmp_path / 'releases'),
        'PAPERGUARD_MARKER': str(marker), 'PAPERGUARD_RELEASE_SOURCE_MANIFEST': '',
        'PROBE_FAIL': failure,
    }
    p = subprocess.run(['bash', str(ROOT / 'ops/deploy-frontend.sh'), str(src), 'fixture'],
                       env=env, capture_output=True, text=True, timeout=15)
    if failure == 'none':
        assert p.returncode == 0, p.stdout + p.stderr
        assert marker.read_text() == 'fixture\n'
        assert (webroot / 'assets/index-fixture.js').exists()
    else:
        assert p.returncode != 0
        assert marker.read_text() == 'old-sha\n'
        assert metadata.read_text() == '{"release":"old-sha"}\n'
        assert (webroot / 'index.html').read_text() == 'old homepage'


def test_nginx_protected_prefixes_cannot_be_shadowed_by_extension_regex():
    source = (ROOT / 'ops/nginx/paperguard.site.conf').read_text()
    for prefix in ('/api/', '/media/', '/assets/'):
        assert f'location ^~ {prefix}' in source


def test_uat_password_predicates_reject_bad_rows_and_allow_dual_source():
    import json
    source = (ROOT / 'ops/uat-verify.sh').read_text()
    reset = re.search(r'"teacher.students.reset_permission" teacher\s+\\\s+\x27([^\x27]+)', source)[1]
    unbind = re.search(r'"teacher.students.unbind_strict" teacher\s+\\\s+\x27([^\x27]+)', source)[1]
    def check(predicate, rows):
        return subprocess.run(['jq', '-e', predicate], input=json.dumps({'students': rows}),
                              text=True, capture_output=True).returncode == 0
    assert not check(reset, [])
    assert not check(reset, [{'can_reset_password': False}])
    assert check(reset, [{'can_reset_password': True}])
    assert check(unbind, [{'can_manage_account': True, 'relation_sources': ['account', 'cohort']}])
    assert not check(unbind, [{'can_manage_account': True, 'relation_sources': ['cohort']}])
