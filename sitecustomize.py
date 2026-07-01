# sitecustomize.py
# Abilita retry/backoff + timeout di default su *tutte* le requests.Session()
# Non richiede modifiche altrove: basta che questo file sia nella root del progetto.
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_ORIG_SESSION = requests.Session
_DISABLE = os.getenv("HTTP_RETRY_DISABLE", "0") == "1"

def _build_retrying_session(*args, **kwargs) -> requests.Session:
    s = _ORIG_SESSION(*args, **kwargs)
    retry = Retry(
        total=5, connect=5, read=5, status=5,
        backoff_factor=0.3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=(
            "HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE", "PATCH"
        ),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=50, pool_maxsize=50)
    s.mount("http://", adapter)
    s.mount("https://", adapter)

    # timeout di default se non viene passato esplicitamente
    _orig_request = s.request
    def _request_with_default_timeout(method, url, **kw):
        if "timeout" not in kw:
            kw["timeout"] = (10, 30)  # (conn_timeout, read_timeout)
        return _orig_request(method, url, **kw)
    s.request = _request_with_default_timeout
    return s

if not _DISABLE:
    requests.Session = _build_retrying_session
