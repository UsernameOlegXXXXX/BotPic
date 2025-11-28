# -*- coding: utf-8 -*-
# BotPhoto5.py — универсальный скрипт для локального запуска (Windows CMD) и планировщика PythonAnywhere.
# Главное:
#   • Авто-фоллбек на системные прокси (trust_env=True) при сетевой ошибке — важно для PythonAnywhere.
#   • Ретраи на 429/5xx, таймауты, понятные логи.
#   • Опциональный запуск только в указанный час Москвы через ENV ONLY_SEND_AT_HOUR; по умолчанию отправляем всегда.
#   • Работает без requests (есть лёгкий shim на urllib).
#
# ENV:
#   BOT_TOKEN           — токен Telegram-бота (обязательно)
#   ONLY_SEND_AT_HOUR   — целый час 0..23 (московское время) для запуска; пусто = всегда
#   MAX_GROUPS          — ограничить число групп за запуск (для теста)
#   EXCLUDE_GROUPS      — CSV имён @Group, которые пропустить
#   SILENT              — "1" для тихого режима логов
#
# Пример (Windows CMD):
#   set BOT_TOKEN=123:ABC
#   python BotPhoto5.py
#
# Пример (PythonAnywhere bash):
#   export BOT_TOKEN=123:ABC
#   python3 /home/USER/BotPhoto5.py

import os
import sys
import time
import random
import math
from datetime import datetime, timedelta
import traceback

# ===== 1) Импорт requests с запасным shim =====
try:
    import requests  # type: ignore
except Exception:
    import urllib.request
    import urllib.error
    import urllib.parse
    import json
    import mimetypes
    import uuid
    from types import SimpleNamespace

    class RequestException(Exception):
        pass

    class SimpleResponse:
        def __init__(self, url, status_code, content, headers):
            self.url = url
            self.status_code = status_code
            self.content = content if isinstance(content, (bytes, bytearray)) else (content.encode("utf-8") if content is not None else b"")
            self._text = None
            self._headers = headers or {}

        @property
        def text(self):
            if self._text is None:
                try:
                    self._text = self.content.decode("utf-8")
                except Exception:
                    try:
                        self._text = self.content.decode("latin-1")
                    except Exception:
                        self._text = str(self.content)
            return self._text

        def json(self):
            try:
                import json as _json
                return _json.loads(self.text)
            except Exception as e:
                raise RequestException(f"Invalid JSON: {e}")

        def iter_content(self, chunk_size=65536):
            b = self.content
            for i in range(0, len(b), chunk_size):
                yield b[i:i + chunk_size]

        def raise_for_status(self):
            if 400 <= self.status_code:
                raise RequestException(f"{self.status_code} Error for url: {self.url}")

        @property
        def headers(self):
            return self._headers

    def _encode_multipart(fields, files):
        boundary = "----WebKitFormBoundary" + uuid.uuid4().hex
        sep = b"\r\n"
        body = bytearray()
        for name, value in (fields or {}).items():
            body.extend(b"--" + boundary.encode() + sep)
            body.extend(f'Content-Disposition: form-data; name="{name}"'.encode() + sep + sep)
            body.extend(str(value).encode("utf-8") + sep)
        for name, filetuple in (files or {}).items():
            filename, filecontent, mime = filetuple
            if mime is None:
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            body.extend(b"--" + boundary.encode() + sep)
            body.extend(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"'.encode() + sep)
            body.extend(f"Content-Type: {mime}".encode() + sep + sep)
            if isinstance(filecontent, str):
                filecontent = filecontent.encode()
            body.extend(filecontent + sep)
        body.extend(b"--" + boundary.encode() + b"--" + sep)
        content_type = f"multipart/form-data; boundary={boundary}"
        return content_type, bytes(body)

    class SimpleSession:
        def __init__(self):
            self.trust_env = True

        def _perform(self, method, url, data=None, json_data=None, files=None, headers=None, timeout=None, stream=False):
            headers = dict(headers or {})
            req_data = None
            if json_data is not None:
                import json as _json
                req_data = _json.dumps(json_data).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            elif files is not None:
                content_type, body = _encode_multipart(data or {}, files)
                req_data = body
                headers.setdefault("Content-Type", content_type)
            elif data is not None:
                req_data = urllib.parse.urlencode(data).encode("utf-8")
                headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

            req = urllib.request.Request(url, data=req_data, method=method)
            for k, v in headers.items():
                req.add_header(k, v)

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    resp_bytes = resp.read()
                    status = resp.getcode()
                    resp_headers = dict(resp.getheaders())
                    return SimpleResponse(url, status, resp_bytes, resp_headers)
            except urllib.error.HTTPError as e:
                try:
                    body = e.read()
                except Exception:
                    body = b""
                status = getattr(e, "code", 0)
                headers = dict(getattr(e, "headers", {}) or {})
                return SimpleResponse(url, status, body, headers)
            except urllib.error.URLError as e:
                raise RequestException(f"Network error: {e}") from e
            except Exception as e:
                raise RequestException(f"Unexpected network error: {e}") from e

        def get(self, url, timeout=None, stream=False, headers=None):
            return self._perform("GET", url, headers=headers, timeout=timeout, stream=stream)

        def post(self, url, json=None, data=None, files=None, headers=None, timeout=None):
            return self._perform("POST", url, data=data, json_data=json, files=files, headers=headers, timeout=timeout)

    requests = SimpleNamespace(Session=SimpleSession, RequestException=RequestException)

# ===== 2) Конфигурация =====

VERSION = "5.0.0"

BOT_TOKEN= "8495793919:AAH2hu3zPA7bz1ZpQDRLB-gcZROXVlJIUhs"
ONLY_SEND_AT_HOUR = os.getenv("ONLY_SEND_AT_HOUR", "").strip()
MAX_GROUPS = int(os.getenv("MAX_GROUPS", "0") or "0")
EXCLUDE_GROUPS = set(x.strip() for x in os.getenv("EXCLUDE_GROUPS", "").split(",") if x.strip())
SILENT = os.getenv("SILENT", "0") == "1"

GROUPS = [
    {"group_name": "@Aeroport_Chat", "latitude": 55.804281, "longitude": 37.539073, "radius": 1500, "city_name": "Аэропорт"},
    {"group_name": "@ChatMedvedkovo", "latitude": 55.879114, "longitude": 37.643698, "radius": 1500, "city_name": "Медведково"},
    {"group_name": "@ChatFili", "latitude": 55.745093, "longitude": 37.495266, "radius": 1500, "city_name": "Фили"},
    {"group_name": "@Nagatino_Life", "latitude": 55.683185, "longitude": 37.621508, "radius": 1500, "city_name": "Нагатино"},
    {"group_name": "@PresnenskiyLife", "latitude": 55.755633, "longitude": 37.536054, "radius": 1500, "city_name": "Пресненский"},
    {"group_name": "@Chertanovo_Chat", "latitude": 55.621415, "longitude": 37.608541, "radius": 2500, "city_name": "Чертаново"},
    {"group_name": "@AkademicheskiyLife", "latitude": 55.686459, "longitude": 37.573192, "radius": 1500, "city_name": "Академический"},
    {"group_name": "@TsarytsinoChat", "latitude": 55.623174, "longitude": 37.672609, "radius": 1500, "city_name": "Царицыно"},
    {"group_name": "@Chat_Lublino", "latitude": 55.680267, "longitude": 37.758087, "radius": 1500, "city_name": "Люблино"},
    {"group_name": "@ChatPerovo", "latitude": 55.747518, "longitude": 37.764708, "radius": 1500, "city_name": "Перово"},
    {"group_name": "@ChatKonkovo", "latitude": 55.652819, "longitude": 37.527410, "radius": 1500, "city_name": "Коньково"},
    {"group_name": "@ChatTushino", "latitude": 55.853520, "longitude": 37.435294, "radius": 2500, "city_name": "Тушино"},
    {"group_name": "@StroginoLifeChat", "latitude": 55.797946, "longitude": 37.409288, "radius": 2500, "city_name": "Строгино"},
    {"group_name": "@Ostankinskiy", "latitude": 55.825926, "longitude": 37.622230, "radius": 2000, "city_name": "Останкинский"},
    {"group_name": "@Golovinskiy_Chat", "latitude": 55.850345, "longitude": 37.517997, "radius": 1500, "city_name": "Головинский"},
    {"group_name": "@ChatIzmailovo", "latitude": 55.785392, "longitude": 37.785219, "radius": 2500, "city_name": "Измайлово"},
    {"group_name": "@Lefortovo_Life", "latitude": 55.763783, "longitude": 37.699887, "radius": 1500, "city_name": "Лефортово"},
    {"group_name": "@Vyhino_Chat", "latitude": 55.718702, "longitude": 37.814263, "radius": 1500, "city_name": "Выхино"},
    {"group_name": "@DolgoprudniyChat", "latitude": 55.938898, "longitude": 37.515196, "radius": 3000, "city_name": "Долгопрудный"},
    {"group_name": "@DmitrovskiyChat", "latitude": 55.889324, "longitude": 37.528188, "radius": 1500, "city_name": "Дмитровский район"},
    {"group_name": "@ChatBrateevo", "latitude": 55.634051, "longitude": 37.770713, "radius": 1500, "city_name": "Братеево"},
    {"group_name": "@OrehovoBorisovo_Chat", "latitude": 55.616802, "longitude": 37.722691, "radius": 2000, "city_name": "Орехово-Борисово"},
    {"group_name": "@ChatBalashiha", "latitude": 55.789676, "longitude": 37.941884, "radius": 2500, "city_name": "Балашиха"},
    {"group_name": "@KapotnyaChat", "latitude": 55.646356, "longitude": 37.803514, "radius": 1500, "city_name": "Капотня"},
    {"group_name": "@Reutov_City", "latitude": 55.762652, "longitude": 37.863734, "radius": 1500, "city_name": "Реутов"},
    {"group_name": "@KrylatskoeLife", "latitude": 55.762740, "longitude": 37.434356, "radius": 2500, "city_name": "Крылатское"},
    {"group_name": "@ChatGolyanovo", "latitude": 55.823951, "longitude": 37.802596, "radius": 1500, "city_name": "Гольяново"},
    {"group_name": "@Hamovniki_Chat", "latitude": 55.723657, "longitude": 37.565791, "radius": 2000, "city_name": "Хамовники"},
    {"group_name": "@DeguninoLife", "latitude": 55.878607, "longitude": 37.514574, "radius": 1500, "city_name": "Дегунино"},
    {"group_name": "@KuntsevoChat", "latitude": 55.738251, "longitude": 37.410601, "radius": 1500, "city_name": "Кунцево"},
    {"group_name": "@TekstilshikiChat", "latitude": 55.719713, "longitude": 37.728036, "radius": 1500, "city_name": "Текстильщики"},
    {"group_name": "@BirulevoLife", "latitude": 55.591536, "longitude": 37.663624, "radius": 1500, "city_name": "Бирюлево"},
    {"group_name": "@ChatYasenevo", "latitude": 55.608131, "longitude": 37.535707, "radius": 1500, "city_name": "Ясенево"},
    {"group_name": "@ChatLubertsy", "latitude": 55.685654, "longitude": 37.892649, "radius": 2500, "city_name": "Люберцы"},
    {"group_name": "@KorolevCityChat", "latitude": 55.925440, "longitude": 37.839757, "radius": 2500, "city_name": "Королев"},
    {"group_name": "@HoroshevskiyChat", "latitude": 55.784675, "longitude": 37.517436, "radius": 2000, "city_name": "Хорошевский"},
    {"group_name": "@ChatBibirevo", "latitude": 55.889980, "longitude": 37.611148, "radius": 1500, "city_name": "Бибирево"},
    {"group_name": "@ChatButovo", "latitude": 55.554355, "longitude": 37.550351, "radius": 2500, "city_name": "Бутово"},
    {"group_name": "@MaryinoLife", "latitude": 55.652504, "longitude": 37.739213, "radius": 1500, "city_name": "Марьино"},
    {"group_name": "@OtradnoeChat", "latitude": 55.860805, "longitude": 37.608422, "radius": 1500, "city_name": "Отрадное"},
    {"group_name": "@TepliyStan_Chat", "latitude": 55.633768, "longitude": 37.490196, "radius": 2000, "city_name": "Теплый стан"},
    {"group_name": "@Mytishi_Chat", "latitude": 55.911749, "longitude": 37.728025, "radius": 3000, "city_name": "Мытищи"},
    {"group_name": "@KrasnogorskCityChat", "latitude": 55.819057, "longitude": 37.354499, "radius": 2500, "city_name": "Красногорск"},
    {"group_name": "@Bogorodskoe_Life", "latitude": 55.809876, "longitude": 37.713010, "radius": 1500, "city_name": "Богородское"},
    {"group_name": "@KotelnikiChat", "latitude": 55.648273, "longitude": 37.867328, "radius": 1500, "city_name": "Котельники"},
    {"group_name": "@Arbat_Chat", "latitude": 55.751442, "longitude": 37.586955, "radius": 1000, "city_name": "Арбат"},
    {"group_name": "@SviblovoChat", "latitude": 55.854135, "longitude": 37.647351, "radius": 1500, "city_name": "Свиблово"},
    {"group_name": "@ChatDorogomilovo", "latitude": 55.732970, "longitude": 37.524982, "radius": 1500, "city_name": "Дорогомилово"},
    {"group_name": "@Podolsk_CityChat", "latitude": 55.434017, "longitude": 37.554218, "radius": 4000, "city_name": "Подольск"},
    {"group_name": "@MeshanskiyChat", "latitude": 55.778118, "longitude": 37.627785, "radius": 1000, "city_name": "Мещанский"},
    {"group_name": "@ZamoskvorechieChat", "latitude": 55.735839, "longitude": 37.635167, "radius": 1500, "city_name": "Замоскворечье"},
    {"group_name": "@ChatYaroslavskiy", "latitude": 55.859952, "longitude": 37.713774, "radius": 2500, "city_name": "Ярославский район"},
    {"group_name": "@ChatRostokino", "latitude": 55.837048, "longitude": 37.653955, "radius": 2500, "city_name": "Ростокино"},
    {"group_name": "@TSAOChat", "latitude": 55.753268, "longitude": 37.622492, "radius": 5000, "city_name": "ЦАО"},
    {"group_name": "@ChatVoykovskiy", "latitude": 55.829346, "longitude": 37.497484, "radius": 1500, "city_name": "Войковский"},
    {"group_name": "@NagorniyChat", "latitude": 55.666194, "longitude": 37.616465, "radius": 2000, "city_name": "Нагорный"},
    {"group_name": "@ChatTverskoy", "latitude": 55.768522, "longitude": 37.608930, "radius": 2500, "city_name": "Тверской район"},
    {"group_name": "@ChatTroparevo", "latitude": 55.666327, "longitude": 37.470980, "radius": 2000, "city_name": "Тропарево"},
    {"group_name": "@ChatOchakovo", "latitude": 55.702545, "longitude": 37.466100, "radius": 2500, "city_name": "Очаково"},
    {"group_name": "@ChatRamenki", "latitude": 55.707557, "longitude": 37.519352, "radius": 2500, "city_name": "Раменки"},
    {"group_name": "@MozhaiskiyChat", "latitude": 55.717254, "longitude": 37.418869, "radius": 1500, "city_name": "Можайский"},
    {"group_name": "@KoptevoChat", "latitude": 55.833176, "longitude": 37.528294, "radius": 2000, "city_name": "Коптево"},
    {"group_name": "@HovrinoChat", "latitude": 55.869041, "longitude": 37.491790, "radius": 1500, "city_name": "Ховрино"},
    {"group_name": "@SchukinoChat", "latitude": 55.800013, "longitude": 37.476993, "radius": 1500, "city_name": "Щукино"},
    {"group_name": "@Nekrasovka_LifeChat", "latitude": 55.697585, "longitude": 37.942765, "radius": 2500, "city_name": "Некрасовка"},
    {"group_name": "@Vidnoe_LifeChat", "latitude": 55.549349, "longitude": 37.696319, "radius": 2500, "city_name": "Видное"},
    {"group_name": "@PutilkovoLifeChat", "latitude": 55.865160, "longitude": 37.392902, "radius": 1500, "city_name": "Путилково"},
    {"group_name": "@Zelenograd_LifeChat", "latitude": 55.989680, "longitude": 37.193698, "radius": 4000, "city_name": "Зеленоград"},
    {"group_name": "@Chat_OdintsovoCity", "latitude": 55.672530, "longitude": 37.271357, "radius": 2000, "city_name": "Одинцово"},
    {"group_name": "@PatrikiOfficial", "latitude": 55.764255, "longitude": 37.596987, "radius": 500, "city_name": "Патриаршие пруды"},
    {"group_name": "@PokrovskoeStreshnevoChat", "latitude": 55.824498, "longitude": 37.455445, "radius": 2000, "city_name": "Покровское-Стрешнево"},
    {"group_name": "@ChatKommunarka", "latitude": 55.569962, "longitude": 37.475104, "radius": 2000, "city_name": "Коммунарка"},
    {"group_name": "@ChatProspektVernadskogo", "latitude": 55.676925, "longitude": 37.499685, "radius": 2000, "city_name": "Проспект Вернадского"},
    {"group_name": "@ChatMetrogorodok", "latitude": 55.833876, "longitude": 37.754528, "radius": 2000, "city_name": "Метрогородок"},
    {"group_name": "@UzhnoportChat", "latitude": 55.705681, "longitude": 37.678192, "radius": 1500, "city_name": "Южнопортовый"},
    {"group_name": "@HimkiChat", "latitude": 55.901370, "longitude": 37.422719, "radius": 4000, "city_name": "Химки"},
    {"group_name": "@MitinoChat", "latitude": 55.845955, "longitude": 37.367747, "radius": 2000, "city_name": "Митино"},
]

# ===== 3) HTTP-сессия, ретраи, прокси-фоллбек =====

SESSION = requests.Session()
try:
    SESSION.trust_env = False
except Exception:
    pass

def _log(msg):
    if not SILENT:
        print(msg, flush=True)

try:
    from requests.adapters import HTTPAdapter  # type: ignore
    try:
        from urllib3.util.retry import Retry  # type: ignore
    except Exception:
        from urllib3.util import Retry  # type: ignore

    retry_kwargs = dict(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.6,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    try:
        retry_strategy = Retry(allowed_methods=frozenset(["GET", "POST"]), **retry_kwargs)
    except TypeError:
        retry_strategy = Retry(method_whitelist=frozenset(["GET", "POST"]), **retry_kwargs)  # type: ignore

    adapter = HTTPAdapter(max_retries=retry_strategy)  # type: ignore
    SESSION.mount("https://", adapter)  # type: ignore
    SESSION.mount("http://", adapter)   # type: ignore
except Exception:
    pass

def _is_pythonanywhere():
    env = os.environ
    return any(k in env for k in ("PYTHONANYWHERE_DOMAIN", "PYTHONANYWHERE_SITE", "PYTHONANYWHERE_USER"))

def _should_try_proxy():
    env = os.environ
    proxy_markers = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")
    return _is_pythonanywhere() or any(k in env for k in proxy_markers)

_PROXY_ENABLED_ONCE = False

def _enable_proxy_mode_once():
    global _PROXY_ENABLED_ONCE
    if _PROXY_ENABLED_ONCE:
        return
    try:
        if hasattr(SESSION, "trust_env") and not SESSION.trust_env:
            SESSION.trust_env = True
            _log("🌐 Переключаюсь в режим через системный прокси (trust_env=True)")
        _PROXY_ENABLED_ONCE = True
    except Exception:
        pass

def _post_with_proxy_fallback(url, **kwargs):
    try:
        return SESSION.post(url, **kwargs)
    except Exception as e:
        if _should_try_proxy():
            _enable_proxy_mode_once()
            return SESSION.post(url, **kwargs)
        raise

def _get_with_proxy_fallback(url, **kwargs):
    try:
        return SESSION.get(url, **kwargs)
    except Exception as e:
        if _should_try_proxy():
            _enable_proxy_mode_once()
            return SESSION.get(url, **kwargs)
        raise

# ===== 4) Утилиты =====

def moscow_now():
    return datetime.utcnow() + timedelta(hours=3)

def get_random_point_in_radius(lat, lon, radius_meters):
    radius_deg = radius_meters / 111000.0
    u = random.random()
    r = math.sqrt(u) * radius_deg
    a = random.uniform(0, 2 * math.pi)
    return lat + r * math.cos(a), lon + r * math.sin(a)

def _download_image_bytes(image_url, timeout=25):
    r = _get_with_proxy_fallback(image_url, timeout=timeout, stream=True)
    if hasattr(r, "raise_for_status"):
        try:
            r.raise_for_status()
        except Exception:
            if getattr(r, "status_code", 500) >= 400:
                raise
    content = b"".join(r.iter_content(chunk_size=65536))
    return content

# ===== 5) Получение КАРТИН (Commons вместо PastVu) =====

def get_pastvu_photos(latitude, longitude, radius):
    """
    Вместо PastVu берём только КАРТИНЫ (живопись/рисунки) из Wikimedia Commons,
    стараясь привязаться к названию района по координатам.
    Возвращаем список словарей: { "image_url": ..., "title": ..., "year": ... }.
    """
    # Определяем название района по совпадению координат и радиуса с записью в GROUPS
    city_name = ""
    try:
        from math import isclose
        for g in GROUPS:
            if isclose(g.get("latitude", 0), latitude, rel_tol=0, abs_tol=1e-6) and \
               isclose(g.get("longitude", 0), longitude, rel_tol=0, abs_tol=1e-6) and \
               int(g.get("radius", 0)) == int(radius):
                city_name = g.get("city_name", "") or ""
                break
    except Exception:
        city_name = ""

    city_name = city_name.strip()
    if not city_name:
        search_terms = ["Moscow painting", "Moscow cityscape painting"]
    else:
        search_terms = [
            f"{city_name} Москва картина",
            f"{city_name} Moscow painting",
            f"{city_name} Moscow cityscape painting",
        ]

    base_url = "https://commons.wikimedia.org/w/api.php"

    def _search_commons(term):
        import urllib.parse
        import re

        params = {
            "action": "query",
            "format": "json",
            "prop": "imageinfo",
            "generator": "search",
            "gsrsearch": term,
            "gsrnamespace": 6,  # файлы (изображения)
            "gsrlimit": 30,
            "iiprop": "url|extmetadata",
            "iiurlwidth": 1280,
        }
        qs = urllib.parse.urlencode(params)
        url = f"{base_url}?{qs}"

        try:
            resp = _get_with_proxy_fallback(url, timeout=20)
        except Exception as e:
            _log(f"   ❌ Commons HTTP ошибка: {e}")
            return []

        sc = getattr(resp, "status_code", 0)
        if sc != 200:
            _log(f"   ❌ Commons статус {sc}")
            return []

        try:
            data = resp.json()
        except Exception as e:
            _log(f"   ❌ Commons не JSON: {e}")
            return []

        query = data.get("query") if isinstance(data, dict) else None
        pages = query.get("pages") if isinstance(query, dict) else None
        if not isinstance(pages, dict):
            return []

        results = []
        for pageid, page in pages.items():
            if not isinstance(page, dict):
                continue
            title = (page.get("title") or "").replace("File:", "").strip()

            imageinfo = page.get("imageinfo") or []
            if not imageinfo or not isinstance(imageinfo, list):
                continue
            ii0 = imageinfo[0] or {}
            image_url = ii0.get("thumburl") or ii0.get("url")
            if not image_url:
                continue

            ext = ii0.get("extmetadata") or {}
            obj_name = ext.get("ObjectName", {}).get("value") or ""
            desc_html = ext.get("ImageDescription", {}).get("value") or ""
            import re as _re
            desc = _re.sub(r"<[^>]+>", "", desc_html)
            cat_str = ext.get("Categories", {}).get("value") or ""

            text_blob = " ".join([title, obj_name, desc, cat_str]).lower()

            # Жёстко отсекаем всё, что не живопись/рисунок
            bad_markers = [
                "postcard", "postcards", "открытка", "открытки",
                "engraving", "гравюра", "гравюры", "lithograph", "литография",
                "photo", "photograph", "фото", "панорама", "panorama",
                "map", "карта", "scheme", "схема", "logo", "логотип",
            ]
            if any(b in text_blob for b in bad_markers):
                continue

            good_markers = [
                "painting", "paintings",
                "oil on canvas", "oil painting", "масло по холсту", "живопись",
                "watercolour", "watercolor", "акварель",
                "drawing", "drawings", "graphite", "рисунок", "рисунки",
            ]
            if not any(g in text_blob for g in good_markers):
                # Если нет прямых маркеров живописи/рисунка — пропускаем
                continue

            year = "год неизвестен"
            m = _re.search(r"(18|19|20)\d{2}", text_blob)
            if m:
                year = m.group(0)

            full_title_parts = []
            if title:
                full_title_parts.append(title)
            if desc:
                full_title_parts.append(desc.strip())
            full_title = "\n\n".join(full_title_parts) if full_title_parts else "Картина с видом района"

            results.append(
                {
                    "image_url": image_url,
                    "title": full_title,
                    "year": year,
                }
            )

        return results

    all_results = []
    total_terms = len(search_terms)
    for idx, term in enumerate(search_terms, 1):
        _log(f"   🎨 Commons поиск ({idx}/{total_terms}): '{term}'")
        res = _search_commons(term)
        all_results.extend(res)
        if len(all_results) >= 20:
            break
        time.sleep(0.5)

    # Убираем дубликаты по URL
    unique_by_url = {}
    for p in all_results:
        url = p.get("image_url")
        if not url:
            continue
        if url not in unique_by_url:
            unique_by_url[url] = p

    photos_list = list(unique_by_url.values())
    _log(f"   🎨 Итог Commons (картины): найдено {len(photos_list)} изображений")
    return photos_list

# ===== 6) Telegram отправка =====

def send_to_telegram(group_name, image_url, description, year, city_name):
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в окружении — остановка.")

    caption = f"🏛 {description}\n📅 {year} год\n📍 {city_name}"
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"

    try:
        data = {"chat_id": group_name, "photo": image_url, "caption": caption}
        resp = _post_with_proxy_fallback(api_url, json=data, timeout=20)
        if getattr(resp, "status_code", 500) == 200:
            _log(f"✅ {city_name}: отправлено по URL")
            return True
        else:
            text = getattr(resp, "text", "") or ""
            if getattr(resp, "status_code", 0) == 429:
                _log(f"⚠️  {city_name}: 429 Too Many Requests, пауза 3 сек и повтор (URL)")
                time.sleep(3)
                resp2 = _post_with_proxy_fallback(api_url, json=data, timeout=20)
                if getattr(resp2, "status_code", 500) == 200:
                    _log(f"✅ {city_name}: отправлено по URL (повтор)")
                    return True
            _log(f"⚠️  {city_name}: не удалось по URL: {text[:300]}")
    except Exception as e:
        _log(f"⚠️  {city_name}: ошибка при отправке по URL: {e}")

    try:
        img_bytes = _download_image_bytes(image_url)
    except Exception as e:
        _log(f"❌ {city_name}: не скачалась картинка: {e}")
        return False

    files = {"photo": ("photo.jpg", img_bytes, "image/jpeg")}
    data_form = {"chat_id": group_name, "caption": caption}

    try:
        resp2 = _post_with_proxy_fallback(api_url, data=data_form, files=files, timeout=30)
        if getattr(resp2, "status_code", 500) == 200:
            _log(f"✅ {city_name}: отправлено как файл")
            return True
        else:
            _log(f"❌ {city_name}: ошибка отправки файла: {getattr(resp2, 'text', '')[:300]}")
            return False
    except Exception as e:
        _log(f"❌ {city_name}: ошибка запроса при отправке файла: {e}")
        return False

# ===== 7) Основной цикл =====

def send_random_photo_to_all_groups():
    sent = 0
    errors = 0

    groups_iter = (g for g in GROUPS if g["group_name"] not in EXCLUDE_GROUPS)
    if MAX_GROUPS > 0:
        from itertools import islice
        groups_iter = islice(groups_iter, MAX_GROUPS)

    for i, group in enumerate(groups_iter, 1):
        city = group["city_name"]
        try:
            _log(f"🔎 {i}. Ищем фото для {city}…")
            photos = get_pastvu_photos(group["latitude"], group["longitude"], group["radius"])
            if not photos:
                _log(f"   ⚠️ {city}: изображения не найдены")
                errors += 1
                continue

            p = random.choice(photos)
            image_url = p.get("image_url")
            if not image_url:
                _log(f"   ⚠️ {city}: у записи нет 'image_url'")
                errors += 1
                continue

            title = p.get("title", "Картина с видом района")
            year = p.get("year", "год неизвестен")

            if send_to_telegram(group["group_name"], image_url, title, year, city):
                sent += 1
            else:
                errors += 1

            time.sleep(1)

        except Exception as e:
            _log(f"   ❌ {city}: исключение в обработке: {e}")
            if not SILENT:
                traceback.print_exc()
            errors += 1

    _log(f"📊 Итог: отправлено {sent}, ошибок {errors}")
    return sent, errors

# ===== 8) Entry point =====

def main():
    print(f"🟢 BotPhoto5 v{VERSION} — старт. Групп в списке: {len(GROUPS)}", flush=True)

    if not BOT_TOKEN:
        print("⛔ Не задан BOT_TOKEN (переменная окружения). Завершаю работу.", flush=True)
        sys.exit(2)

    now = moscow_now()
    print(f"⏰ Московское время сейчас: {now:%Y-%m-%d %H:%M:%S}", flush=True)

    if ONLY_SEND_AT_HOUR != "":
        try:
            target_hour = int(ONLY_SEND_AT_HOUR)
            if not (0 <= target_hour <= 23):
                raise ValueError
        except ValueError:
            print(f"⚠️ ONLY_SEND_AT_HOUR='{ONLY_SEND_AT_HOUR}' невалиден — игнор, отправляю немедленно.", flush=True)
        else:
            if now.hour != target_hour:
                print(f"🛑 Запуск вне окна ONLY_SEND_AT_HOUR={target_hour}. Ничего не отправляю.", flush=True)
                return

    sent, errors = send_random_photo_to_all_groups()
    print(f"✅ Готово. Успехов: {sent}. Ошибок: {errors}.", flush=True)

    if _is_pythonanywhere():
        print("ℹ️ Запущено в среде PythonAnywhere.", flush=True)
        if errors and not _PROXY_ENABLED_ONCE and _should_try_proxy():
            print("ℹ️ Похоже, сеть недоступна без прокси. При следующем запуске будет включён trust_env=True.", flush=True)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"💥 Неперехваченное исключение: {e}", flush=True)
        if not SILENT:
            traceback.print_exc()
        sys.exit(1)
