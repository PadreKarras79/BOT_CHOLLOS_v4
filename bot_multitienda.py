from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
import html
import json
import os
import random
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests
from playwright.sync_api import sync_playwright
import requests

# ==========================================
# 1. CONFIGURACIÓN Y CREDENCIALES
# ==========================================
MEEPA_USER = os.getenv("MEEPA_USER", "")
MEEPA_PASS = os.getenv("MEEPA_PASS", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
AMAZON_TAG = os.getenv("AMAZON_TAG", "")

URL_MEEPA_BASE = "https://meepa.reyesdelchollo.com"
URL_MEEPA_LOGIN = f"{URL_MEEPA_BASE}/login"
URL_MEEPA_SESSIONS = f"{URL_MEEPA_BASE}/sessions"

URLS_AMAZON_CUPONES = [
    "https://www.amazon.es/blackfriday?promotionsSearchPageSize=60&bubble-id=deals-collection-coupons",
]

ARCHIVO_HISTORIAL = "historial_multitienda.json"

# Cupos por bloque en cada ejecución
MAX_AMAZON_POR_PASADA = 40
MAX_CUPONES_AMAZON_POR_PASADA = 250
MAX_TECH_POR_PASADA = 20
MAX_OTRAS_TIENDAS_POR_PASADA = 25
MAX_POR_TIENDA_EN_PASADA = 4

# Pausa segura para no superar el límite de 20 mensajes/minuto de Telegram
PAUSA_TELEGRAM_SEGUNDOS = 2.2

QUERY_PMH = "utf8=%E2%9C%93&order_by=discount+DESC&range=48&pmh=1&search=&commit=Filtrar&sellers%5B%5D=Todos+los+vendedores&seller_type=normal"
QUERY_TOP = "utf8=%E2%9C%93&order_by=discount+DESC&range=24&search=&commit=Filtrar&sellers%5B%5D=Todos+los+vendedores&seller_type=normal"
QUERY_SEMANA = "utf8=%E2%9C%93&order_by=discount+DESC&range=168&search=&commit=Filtrar&sellers%5B%5D=Todos+los+vendedores&seller_type=normal"

BUSQUEDAS_TECH = [
    "portatil",
    "ordenador",
    "camara",
    "objetivo",
    "monitor",
    "ssd",
    "tarjeta grafica",
    "auriculares",
    "tablet",
    "televisor",
    "proyector",
    "microfono",
]

PALABRAS_CLAVE_TECH = [
    "portátil", "portatil", "laptop", "macbook", "notebook", "ordenador",
    "pc ", "sobremesa", "mini pc", "ryzen", "intel core", "geforce", "rtx",
    "tarjeta gráfica", "placa base", "memoria ram", "ssd", "nvme", "disco duro",
    "monitor", "cámara", "camara", "réflex", "reflex", "mirrorless", "objetivo",
    "videocámara", "videocamara", "trípode", "tripode", "gimbal",
    "televisor", "smart tv", "oled", "qled", "proyector", "barra de sonido",
    "auriculares", "altavoz", "micrófono", "microfono", "webcam", "tablet",
    "ipad", "smartphone", "móvil", "movil", "router", "wifi 6", "wifi 7",
    "teclado mecánico", "ratón gaming", "impresora",
]

EXCLUSIONES_TECH = [
    "paddle",
    "surf",
    "hinchable",
    "soporte para",
    "funda",
    "carcasa",
    "cordón",
    "cordon",
    "protector de pantalla",
    "correa para",
    "peluche",
    "juguete",
    "muñeco",
    "muneco",
]

TIENDAS_TECNOLOGIA = [
    "/deals/amazon",
    "/deals/pccomponentes",
    "/deals/mediamarkt",
    "/deals/fnac",
    "/deals/elcorteingles",
    "/deals/alternate",
    "/deals/coolmod",
    "/deals/ldlc",
    "/deals/gangaelectronica",
    "/deals/electronicavicente",
    "/deals/phonehouse",
]

TIENDAS_ALTA_CONVERSION = [
    "/deals/songmics",
    "/deals/alternate",
    "/deals/ldlc",
    "/deals/motoblouz",
    "/deals/phonehouse",
    "/deals/fnac",
    "/deals/elcorteingles",
    "/deals/pccomponentes",
    "/deals/mediamarkt",
    "/deals/decathlon",
    "/deals/miravia",
    "/deals/driffle",
    "/deals/farmaciamarket",
    "/deals/gangaelectronica",
    "/deals/electronicavicente",
    "/deals/alltricks",
    "/deals/leroymerlin",
    "/deals/carrefour",
    "/deals/coolmod",
]

PALABRAS_EXCLUIDAS = [
    "tapa blanda",
    "tapa dura",
    "versión kindle",
    "audiolibro",
    "cuaderno de actividades",
]

HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        " (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
}


# ==========================================
# 2. CONTROL DE DUPLICADOS, VARIANTES Y CATEGORÍAS
# ==========================================
def cargar_historial():
    if os.path.exists(ARCHIVO_HISTORIAL):
        try:
            with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()


def guardar_historial(historial_set):
    with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as f:
        json.dump(list(historial_set)[-12000:], f, indent=2)


def generar_clave_variante(tienda, titulo):
    limpio = re.sub(r"[^\w\sáéíóúñü]", " ", titulo.lower())
    palabras = [p for p in limpio.split() if len(p) > 1]
    base = "_".join(palabras[:6])
    return f"VAR_{tienda.lower()}_{base}"


def limpiar_precio(texto):
    if not texto:
        return 0.0
    limpio = str(texto).strip()
    if re.search(r"\d+\.\d{3},\d{2}", limpio):
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "," in limpio and "." not in limpio:
        limpio = limpio.replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", limpio)
    return float(match.group(1)) if match else 0.0


def extraer_precio_real_tarjeta_meepa(card):
    for elem in card.find_all(["strong", "div", "span"]):
        txt = elem.get_text(" ", strip=True)
        if "€" in txt and "%" not in txt:
            val = limpiar_precio(txt)
            if val > 0:
                return val
    return 0.0


def limpiar_nombre_tienda(texto):
    limpio = re.sub(r"\s*externos\d+$", "", texto, flags=re.I)
    limpio = re.sub(r"\d+$", "", limpio).strip()
    return limpio.capitalize() if limpio else "Tienda Online"


def es_producto_excluido(titulo):
    t = titulo.lower()
    return any(palabra in t for palabra in PALABRAS_EXCLUIDAS)


def es_producto_tecnologia(titulo):
    t = f" {titulo.lower()} "
    if any(exc in t for exc in EXCLUSIONES_TECH):
        return False
    return any(palabra in t for palabra in PALABRAS_CLAVE_TECH)


# ==========================================
# 3. EXTRACCIÓN DIRECTA CON PLAYWRIGHT (SESIÓN CALIENTE) DE AMAZON CUPONES
# ==========================================
def superar_filtros_amazon(page):
    """
    Si Amazon muestra la pantalla intermedia de 'Continuar comprando' / 'Continue shopping'
    o el banner de cookies, hace clic automático para desbloquear la sesión.
    """
    selectores_antibot = [
        "button:has-text('Continuar comprando')",
        "button:has-text('Continue shopping')",
        "a:has-text('Continuar comprando')",
        "a:has-text('Continue shopping')",
        "input[type='submit'][value*='Continuar']",
        "input[type='submit'][value*='Continue']",
    ]
    for sel in selectores_antibot:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1200):
                print(f"🔓 Pulsando botón de paso de Amazon: {sel}")
                btn.click()
                page.wait_for_timeout(2500)
                break
        except Exception:
            pass

    try:
        btn_cookies = page.locator("#sp-cc-accept")
        if btn_cookies.is_visible(timeout=1500):
            btn_cookies.click()
            page.wait_for_timeout(1000)
    except Exception:
        pass


def scrapear_amazon_cupones_playwright():
    """Extrae todos los cupones disponibles desde la cuadrícula DealsX de Amazon.es."""
    resultados = []
    tarjetas_por_asin = {}
    debug_dir = "debug_amazon"
    os.makedirs(debug_dir, exist_ok=True)

    def norm(value):
        return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ")).strip()

    def parse_cards(html_page):
        soup = BeautifulSoup(html_page, "html.parser")
        cards = soup.select(
            '[data-testid="product-card"][data-asin], '
            '[data-asin][data-csa-c-filtered*="deals-collection-coupons"], '
            '[data-asin][data-csa-c-item-type="deal"]'
        )
        added = 0
        stats = {
            "sin_texto_cupon": 0,
            "sin_titulo": 0,
            "sin_precio": 0,
            "sin_descuento": 0,
            "aceptadas": 0,
        }

        def money_values(raw):
            vals = []
            for m in re.finditer(
                r'(?<!\w)(\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*€',
                raw or "",
            ):
                before = (raw[max(0, m.start() - 28):m.start()] or "").lower()
                if any(term in before for term in ("ahorra", "descuento", "cupón", "cupon", "rebaja")):
                    continue
                value = _normalizar_importe_es(m.group(1))
                if 0.5 <= value <= 100000:
                    vals.append(value)
            return vals

        for card in cards:
            asin = norm(card.get("data-asin")).upper()
            if not re.fullmatch(r"[A-Z0-9]{10}", asin):
                continue

            txt = norm(card.get_text(" ", strip=True))
            low = txt.lower()
            if "cupón" not in low and "cupon" not in low:
                stats["sin_texto_cupon"] += 1
                continue
            if "patrocinado" in low:
                continue

            title_el = card.select_one(
                '[data-testid="product-card-title"], '
                '[class*="ProductCard-module__title"], '
                '[class*="title"]'
            )
            img = card.find("img")
            title = norm(title_el.get_text(" ", strip=True) if title_el else "")
            if not title and img:
                title = norm(img.get("alt", ""))
            if not title:
                link0 = card.select_one('a[href*="/dp/"]')
                title = norm((link0.get("title", "") or link0.get_text(" ", strip=True)) if link0 else "")
            if len(title) < 8 or es_producto_excluido(title):
                stats["sin_titulo"] += 1
                continue

            price_values = []
            for selector in (
                '[class*="priceToPay"] .a-offscreen',
                '[data-testid*="price"] .a-offscreen',
                '.a-price .a-offscreen',
                '[class*="price"] .a-offscreen',
            ):
                for node in card.select(selector):
                    raw = norm(node.get("content") or node.get_text(" ", strip=True))
                    val = _normalizar_importe_es(raw)
                    if 0.5 <= val <= 100000:
                        price_values.append(val)

            if not price_values:
                price_values.extend(money_values(txt))

            price_values = list(dict.fromkeys(round(x, 2) for x in price_values))
            base_price = price_values[0] if price_values else 0.0
            if base_price <= 0:
                stats["sin_precio"] += 1
                continue

            m_pay = re.search(
                r"Pagas\s*(\d{1,3}(?:\.\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*€\s*con un cup[oó]n",
                txt, re.I
            )
            m_eur = re.search(
                r"(?:Ahorra|Descuento de)\s*(\d+(?:[.,]\d{1,2})?)\s*€\s*con un cup[oó]n",
                txt, re.I
            )
            m_pct = re.search(
                r"(?:Ahorra|Descuento de)\s*(\d+(?:[.,]\d{1,2})?)\s*%\s*con un cup[oó]n",
                txt, re.I
            )

            if not (m_pay or m_eur or m_pct):
                m_pct = re.search(r"Ahorra\s*(\d+(?:[.,]\d+)?)\s*%", txt, re.I)
                m_eur = re.search(r"Ahorra\s*(\d+(?:[.,]\d{1,2})?)\s*€", txt, re.I)

            if m_pay:
                coupon_price = _normalizar_importe_es(m_pay.group(1))
                label = f"Pagas {coupon_price:.2f}€ con cupón"
            elif m_eur:
                discount = _normalizar_importe_es(m_eur.group(1))
                coupon_price = round(max(base_price - discount, 0), 2)
                label = f"Cupón de -{discount:.2f}€ (precio mostrado {base_price:.2f}€)"
            elif m_pct:
                pct = float(m_pct.group(1).replace(",", "."))
                coupon_price = round(base_price * (1 - pct / 100), 2)
                label = f"Cupón del {pct:g}% (precio mostrado {base_price:.2f}€)"
            else:
                stats["sin_descuento"] += 1
                continue

            if coupon_price <= 0 or coupon_price > base_price:
                stats["sin_descuento"] += 1
                continue

            image = ""
            if img:
                image = img.get("src") or img.get("data-src") or ""
                if not image:
                    srcset = img.get("srcset", "")
                    image = srcset.split(",")[0].strip().split(" ")[0] if srcset else ""

            url = f"https://www.amazon.es/dp/{asin}"
            if AMAZON_TAG:
                url += f"?tag={AMAZON_TAG}"

            if asin not in tarjetas_por_asin:
                added += 1
            tarjetas_por_asin[asin] = {
                "asin": asin,
                "titulo": title,
                "precio_base": base_price,
                "precio_con_cupon": coupon_price,
                "descuento_pct": round((base_price - coupon_price) / base_price * 100, 2),
                "etiqueta": label,
                "url": url,
                "imagen": image,
            }
            stats["aceptadas"] += 1

        return added, len(cards), stats

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                context = browser.new_context(
                    user_agent=HEADERS_NAVEGADOR["User-Agent"],
                    locale="es-ES",
                    timezone_id="Europe/Madrid",
                    viewport={"width": 1440, "height": 1100},
                    extra_http_headers={"Accept-Language": "es-ES,es;q=0.9"},
                )
                page = context.new_page()

                for page_no, url in enumerate(URLS_AMAZON_CUPONES, start=1):
                    print(f"🌐 Abriendo página de cupones Amazon {page_no}: {url}")
                    try:
                        response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
                        page.wait_for_timeout(4500)
                        superar_filtros_amazon(page)

                        print(
                            f"📄 Amazon página {page_no}: HTTP "
                            f"{response.status if response else 'sin respuesta'} | "
                            f"URL final: {page.url} | Título: {page.title()!r}"
                        )
                        try:
                            page.wait_for_selector(
                                '[data-testid="discount-asin-grid"], '
                                '[data-testid="product-card"], '
                                '[data-asin][data-csa-c-item-type="deal"]',
                                timeout=15000,
                            )
                        except Exception:
                            print("⚠️ No apareció la cuadrícula de descuentos en 15 segundos.")

                        sin_novedades = 0
                        for paso in range(35):
                            if len(tarjetas_por_asin) >= MAX_CUPONES_AMAZON_POR_PASADA:
                                break
                            html_actual = page.content()
                            nuevas, tarjetas_dom, stats_parse = parse_cards(html_actual)
                            print(
                                f"🔎 Amazon scroll {paso + 1}: "
                                f"{tarjetas_dom} tarjetas DOM, +{nuevas} ASIN nuevos, "
                                f"{len(tarjetas_por_asin)} acumulados."
                            )
                            sin_novedades = sin_novedades + 1 if nuevas == 0 else 0
                            if sin_novedades >= 5:
                                break
                            page.mouse.wheel(0, 1200)
                            page.wait_for_timeout(1000)

                        parse_cards(page.content())

                        if not tarjetas_por_asin:
                            with open(os.path.join(debug_dir, f"amazon_cupones_pagina_{page_no}.html"), "w", encoding="utf-8") as f:
                                f.write(page.content())
                            page.screenshot(
                                path=os.path.join(debug_dir, f"amazon_cupones_pagina_{page_no}.png"),
                                full_page=True,
                            )
                    except Exception as page_error:
                        print(f"⚠️ Error en página de cupones {page_no}: {type(page_error).__name__}: {page_error}")

                browser.close()
            finally:
                try:
                    context.close()
                except Exception:
                    pass

        for item in tarjetas_por_asin.values():
            asin = item["asin"]
            title = item["titulo"]
            resultados.append({
                "id_unico": f"CUPON_AMZ_{asin}",
                "asin": asin,
                "clave_variante": generar_clave_variante("Amazon", title),
                "es_pmh": False,
                "es_tech": es_producto_tecnologia(title),
                "tienda": "Amazon",
                "titulo": title,
                "precio_meepa": item["precio_con_cupon"],
                "descuento_pct": item["descuento_pct"],
                "cupon": True,
                "texto_cupon": item["etiqueta"].replace(".", ","),
                "url": item["url"],
                "imagen": item["imagen"],
            })

        resultados.sort(
            key=lambda x: (
                x.get("descuento_pct", 0),
                x.get("precio_meepa", 0),
            ),
            reverse=True,
        )
        resultados = resultados[:MAX_CUPONES_AMAZON_POR_PASADA]
        print(f"✅ Cupones Amazon extraídos: {len(resultados)}")
    except Exception as e:
        print(f"⚠️ Error iniciando Playwright para cupones Amazon: {type(e).__name__}: {e}")

    return resultados


# ==========================================
# 4. VERIFICACIÓN EN VIVO DE PRECIOS
# ==========================================
def _normalizar_importe_es(texto):
    """Convierte importes españoles/europeos a float sin confundir millares."""
    if texto is None:
        return 0.0
    s = str(texto).replace("\xa0", " ").replace("€", "").strip()
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return 0.0
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(?:\.\d{3})+", s):
        s = s.replace(".", "")
    elif s.count(".") > 1:
        parts = s.split(".")
        s = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def inspeccionar_ficha_amazon_movil(asin, precio_referencia=0.0):
    url = f"https://www.amazon.es/dp/{asin}"
    candidatos = []
    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome124",
            headers={
                "Accept-Language": "es-ES,es;q=0.9",
                "Referer": "https://www.amazon.es/",
            },
            timeout=18,
            allow_redirects=True,
        )
        body = resp.text or ""

        if resp.status_code in (404, 410):
            print(f"⏭️ Amazon {asin}: ficha inexistente (HTTP {resp.status_code}).")
            return False, 0.0, False

        soup = BeautifulSoup(body, "html.parser")
        page_text = soup.get_text(" ", strip=True).lower()
        if any(x in page_text for x in (
            "temporalmente agotado", "no disponible por el momento",
            "currently unavailable", "producto no disponible",
        )):
            print(f"⏭️ Amazon {asin}: producto no disponible.")
            return False, 0.0, False

        selectors = [
            "#corePrice_feature_div .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-offscreen",
            "#priceblock_dealprice",
            "#priceblock_ourprice",
            ".apexPriceToPay .a-offscreen",
            ".a-price .a-offscreen",
            "span.a-price-whole",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                raw = node.get("content") or node.get_text(" ", strip=True)
                value = _normalizar_importe_es(raw)
                if 0.5 <= value <= 100000:
                    candidatos.append(value)

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or script.get_text() or "")
                value = extraer_precio_jsonld(data)
                if 0.5 <= value <= 100000:
                    candidatos.append(value)
            except Exception:
                continue

        candidatos = sorted(set(round(x, 2) for x in candidatos if x > 0))

        if candidatos and precio_referencia > 0:
            elegido = min(candidatos, key=lambda x: abs(x - precio_referencia))
            diferencia = abs(elegido - precio_referencia) / max(precio_referencia, 1.0)

            if diferencia <= 0.35:
                return True, elegido, True

            print(
                f"⚠️ Amazon {asin}: precios encontrados {candidatos[:8]} no "
                f"coinciden con MEEPA ({precio_referencia:.2f}€); "
                "se conserva referencia MEEPA sin verificar."
            )
            return True, precio_referencia, False

        if candidatos:
            return True, min(candidatos), False

        if precio_referencia > 0:
            motivo = (
                f"HTTP {resp.status_code}" if resp.status_code != 200
                else "Amazon no expuso un precio identificable"
            )
            print(
                f"ℹ️ Amazon {asin}: {motivo}; se conserva MEEPA "
                f"{precio_referencia:.2f}€ sin verificación."
            )
            return True, precio_referencia, False

    except Exception as exc:
        if precio_referencia > 0:
            print(
                f"ℹ️ Amazon {asin}: error de consulta ({type(exc).__name__}); "
                f"se conserva MEEPA {precio_referencia:.2f}€ sin verificar."
            )
            return True, precio_referencia, False
        print(f"⏭️ Amazon {asin}: no se pudo consultar ({type(exc).__name__}).")

    return False, 0.0, False


def extraer_precio_jsonld(data):
    if isinstance(data, dict):
        if "price" in data:
            val = limpiar_precio(data["price"])
            if val > 0:
                return val
        for v in data.values():
            res = extraer_precio_jsonld(v)
            if res > 0:
                return res
    elif isinstance(data, list):
        for item in data:
            res = extraer_precio_jsonld(item)
            if res > 0:
                return res
    return 0.0


def verificar_chollo_en_tienda(chollo):
    url = chollo["url"]
    price_ref = float(chollo.get("precio_meepa") or 0)
    chollo["_precio_verificado"] = False

    if chollo["tienda"] == "Amazon" and chollo.get("asin"):
        valid, price, verified = inspeccionar_ficha_amazon_movil(
            chollo["asin"], price_ref
        )
        chollo["_precio_verificado"] = verified
        return chollo, valid, price

    try:
        resp = cffi_requests.get(
            url,
            impersonate="chrome124",
            headers={
                "Accept-Language": "es-ES,es;q=0.9",
                "User-Agent": HEADERS_NAVEGADOR.get("User-Agent", ""),
            },
            timeout=18,
            allow_redirects=True,
        )

        if resp.status_code in (404, 410):
            print(
                f"⏭️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
                f"ficha inexistente HTTP {resp.status_code}."
            )
            return chollo, False, 0.0

        body = resp.text or ""
        soup = BeautifulSoup(body, "html.parser")
        page_text = soup.get_text(" ", strip=True).lower()

        unavailable = [
            "producto agotado", "temporalmente agotado",
            "no disponible por el momento", "producto no disponible",
            "out of stock", "currently unavailable",
            "este producto ya no está disponible",
        ]
        if any(term in page_text for term in unavailable):
            print(
                f"⏭️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
                "la tienda indica que está agotado/no disponible."
            )
            return chollo, False, 0.0

        price_candidates = []
        for prop in (
            "product:price:amount", "og:price:amount",
            "twitter:data1", "price",
        ):
            meta = (
                soup.find("meta", {"property": prop})
                or soup.find("meta", {"name": prop})
                or soup.find("meta", {"itemprop": prop})
            )
            if meta and meta.get("content"):
                val = _normalizar_importe_es(meta["content"])
                if 0.5 <= val <= 100000:
                    price_candidates.append(val)

        def recoger_precios_jsonld(obj):
            encontrados = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in ("price", "lowprice", "highprice"):
                        val = _normalizar_importe_es(value)
                        if 0.5 <= val <= 100000:
                            encontrados.append(val)
                    else:
                        encontrados.extend(recoger_precios_jsonld(value))
            elif isinstance(obj, list):
                for item in obj:
                    encontrados.extend(recoger_precios_jsonld(item))
            return encontrados

        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(script.string or script.get_text() or "")
                price_candidates.extend(recoger_precios_jsonld(data))
            except Exception:
                continue

        for selector in (
            '[itemprop="price"]', '[data-price]',
            '.current-price span[content]', '.product-price',
            '.price--main .money', 'span.woocommerce-Price-amount',
            '.precio-actual', '.final-price', '.price .amount',
            '[class*="price"] [class*="amount"]',
        ):
            for node in soup.select(selector):
                raw = (
                    node.get("content") or node.get("data-price")
                    or node.get_text(" ", strip=True)
                )
                val = _normalizar_importe_es(raw)
                if 0.5 <= val <= 100000:
                    price_candidates.append(val)

        price_candidates = sorted(set(round(x, 2) for x in price_candidates))
        if price_candidates and price_ref > 0:
            price = min(price_candidates, key=lambda x: abs(x - price_ref))
            diff = abs(price - price_ref) / max(price_ref, 1.0)
            if diff <= 0.20:
                chollo["_precio_verificado"] = True
                return chollo, True, price

            print(
                f"⚠️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
                f"precio leído {price:.2f}€ no coincide con MEEPA "
                f"{price_ref:.2f}€; se publica referencia MEEPA sin verificar."
            )
            return chollo, True, price_ref

        if price_candidates:
            return chollo, True, price_candidates[0]

        if price_ref > 0 and (
            resp.status_code in (200, 202, 403, 408, 429, 500, 502, 503, 504)
            or (300 <= resp.status_code < 500 and resp.status_code not in (404, 410))
            or len(body) > 500
        ):
            motivo = f"HTTP {resp.status_code}, precio no extraíble"
            print(
                f"ℹ️ {chollo['tienda']} | {chollo['titulo'][:45]}: {motivo}; "
                f"se conserva MEEPA {price_ref:.2f}€ sin verificar."
            )
            return chollo, True, price_ref

        print(
            f"⏭️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
            f"no validable (HTTP {resp.status_code}, {len(body)} bytes)."
        )
        return chollo, False, 0.0

    except Exception as exc:
        if price_ref > 0:
            print(
                f"ℹ️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
                f"error de consulta {type(exc).__name__}; se conserva "
                f"MEEPA {price_ref:.2f}€ sin verificar."
            )
            return chollo, True, price_ref
        print(
            f"⏭️ {chollo['tienda']} | {chollo['titulo'][:45]}: "
            f"error de verificación {type(exc).__name__}."
        )
        return chollo, False, 0.0


# ==========================================
# 5. SESIÓN Y EXTRACCIÓN DE MEEPA
# ==========================================
def crear_sesion_meepa():
    for intento in range(1, 5):
        try:
            sesion = requests.Session()
            sesion.headers.update(HEADERS_NAVEGADOR)

            resp_login = sesion.get(URL_MEEPA_LOGIN, timeout=35)
            resp_login.raise_for_status()

            soup = BeautifulSoup(resp_login.text, "html.parser")
            token_input = soup.find("input", {"name": "authenticity_token"})
            if not token_input:
                raise RuntimeError(
                    "No se encontró el authenticity_token en Meepa /login."
                )

            payload = {
                "utf8": "✓",
                "authenticity_token": token_input["value"],
                "username": MEEPA_USER,
                "password": MEEPA_PASS,
                "commit": "Iniciar sesión",
            }

            resp_post = sesion.post(
                URL_MEEPA_SESSIONS,
                data=payload,
                headers={"Referer": URL_MEEPA_LOGIN},
                timeout=40,
            )
            resp_post.raise_for_status()

            if 'name="username"' in resp_post.text:
                raise PermissionError(
                    "Fallo de login en Meepa. Revisa MEEPA_USER y MEEPA_PASS."
                )

            print("✅ [Meepa] Login correcto.")
            return sesion

        except Exception as e:
            print(f"⚠️ Intento {intento}/4 de login en Meepa fallido: {e}")
            if intento < 4:
                time.sleep(8 * intento)

    print("❌ Meepa no responde tras 4 intentos. Se omite esta pasada sin error.")
    return None


def obtener_rutas_tiendas(sesion_meepa):
    resp_home = sesion_meepa.get(URL_MEEPA_BASE, timeout=20)
    if 'name="username"' in resp_home.text or "/login" in resp_home.url:
        return None

    soup = BeautifulSoup(resp_home.text, "html.parser")
    rutas = {}

    for enlace in soup.find_all("a", href=True):
        href = enlace["href"].split("?")[0]
        if href.startswith("/deals/") and len(href) > 7:
            nombre = enlace.get_text(strip=True) or href.replace("/deals/", "")
            rutas[href] = limpiar_nombre_tienda(nombre)

    return rutas


def extraer_tarjetas_pagina(soup, nombre_tienda_ruta, es_pmh):
    encontrados = []
    tarjetas = soup.find_all("div", class_="offer")

    for card in tarjetas:
        offer_id = card.get("data-asin", "")

        link_tag = card.find("a", class_="title-link")
        if not link_tag or not link_tag.get("href"):
            continue
        url_destino = link_tag["href"]

        card_img_div = card.find("div", class_="card-image")
        tienda_raw = card_img_div.get("data-shop", "") if card_img_div else ""

        asin = ""
        match_amz = re.search(r"amazon\.es.*/dp/([A-Z0-9]{10})", url_destino)
        if match_amz:
            asin = match_amz.group(1)
            url_destino = f"https://www.amazon.es/dp/{asin}"
            if AMAZON_TAG:
                url_destino += f"?tag={AMAZON_TAG}"
            tienda_detectada = "Amazon"
        elif tienda_raw:
            tienda_detectada = limpiar_nombre_tienda(tienda_raw)
        else:
            tienda_detectada = nombre_tienda_ruta

        precio_meepa = extraer_precio_real_tarjeta_meepa(card)
        if precio_meepa < 5.0:
            continue

        content_div = card.find("div", class_="card-content")
        titulo = (
            content_div.get("data-balloon", "").strip()
            if content_div and content_div.get("data-balloon")
            else link_tag.get_text(strip=True)
        )
        if not titulo or es_producto_excluido(titulo):
            continue

        img_tag = card.find("img")
        imagen = (
            img_tag.get("src") or img_tag.get("data-src") if img_tag else None
        )
        texto_tarjeta = card.get_text(" ").lower()
        tiene_cupon = ("cupón" in texto_tarjeta) or ("cupon" in texto_tarjeta)

        encontrados.append({
            "id_unico": f"MEEPA_{offer_id or asin or url_destino[:50]}",
            "asin": asin,
            "clave_variante": generar_clave_variante(tienda_detectada, titulo),
            "es_pmh": es_pmh,
            "es_tech": es_producto_tecnologia(titulo),
            "tienda": tienda_detectada,
            "titulo": titulo,
            "precio_meepa": precio_meepa,
            "cupon": tiene_cupon,
            "texto_cupon": "",
            "url": url_destino,
            "imagen": imagen,
        })

    return encontrados


def scrapear_amazon_meepa(sesion_meepa):
    candidatos_amz = []
    for query, es_pmh, max_pags in [
        (QUERY_PMH, True, 6),
        (QUERY_TOP, False, 6),
        (QUERY_SEMANA, False, 8),
    ]:
        for pagina in range(1, max_pags + 1):
            url = f"{URL_MEEPA_BASE}/deals/amazon?{query}&page={pagina}"
            try:
                resp = sesion_meepa.get(url, timeout=15)
                if 'name="username"' in resp.text:
                    return None
                soup = BeautifulSoup(resp.text, "html.parser")
                items = extraer_tarjetas_pagina(soup, "Amazon", es_pmh=es_pmh)
                if not items:
                    break
                candidatos_amz.extend(items)
            except Exception:
                break
            time.sleep(random.uniform(0.15, 0.25))

    vistos_id = set()
    vistos_var = set()
    unicos = []
    for c in candidatos_amz:
        if (
            c["id_unico"] not in vistos_id
            and c["clave_variante"] not in vistos_var
        ):
            vistos_id.add(c["id_unico"])
            vistos_var.add(c["clave_variante"])
            unicos.append(c)

    return unicos


def scrapear_tecnologia_meepa(sesion_meepa, rutas_disponibles):
    candidatos_tech = []

    palabras_muestra = random.sample(BUSQUEDAS_TECH, min(6, len(BUSQUEDAS_TECH)))
    tiendas_busqueda = [
        t for t in ["/deals/amazon", "/deals/pccomponentes", "/deals/mediamarkt", "/deals/fnac"]
        if t in rutas_disponibles or t == "/deals/amazon"
    ]

    for ruta in tiendas_busqueda:
        nombre_tienda = rutas_disponibles.get(ruta, "Amazon")
        for palabra in palabras_muestra:
            query_busqueda = QUERY_PMH.replace("search=", f"search={requests.utils.quote(palabra)}")
            url = f"{URL_MEEPA_BASE}{ruta}?{query_busqueda}"
            try:
                resp = sesion_meepa.get(url, timeout=10)
                if 'name="username"' in resp.text:
                    return None
                soup = BeautifulSoup(resp.text, "html.parser")
                items = extraer_tarjetas_pagina(soup, nombre_tienda, es_pmh=True)
                solo_tech = [i for i in items if i["es_tech"]]
                candidatos_tech.extend(solo_tech[:5])
            except Exception:
                continue
            time.sleep(random.uniform(0.15, 0.25))

    for ruta in TIENDAS_TECNOLOGIA:
        if ruta not in rutas_disponibles and ruta != "/deals/amazon":
            continue
        nombre_tienda = rutas_disponibles.get(ruta, "Amazon")
        for query, es_pmh in [(QUERY_PMH, True), (QUERY_TOP, False)]:
            url = f"{URL_MEEPA_BASE}{ruta}?{query}&page=1"
            try:
                resp = sesion_meepa.get(url, timeout=10)
                soup = BeautifulSoup(resp.text, "html.parser")
                items = extraer_tarjetas_pagina(soup, nombre_tienda, es_pmh=es_pmh)
                solo_tech = [i for i in items if i["es_tech"]]
                candidatos_tech.extend(solo_tech[:6])
            except Exception:
                continue
            time.sleep(random.uniform(0.15, 0.25))

    vistos_id = set()
    vistos_var = set()
    por_tienda = defaultdict(list)

    for c in candidatos_tech:
        if (
            c["id_unico"] not in vistos_id
            and c["clave_variante"] not in vistos_var
        ):
            vistos_id.add(c["id_unico"])
            vistos_var.add(c["clave_variante"])
            por_tienda[c["tienda"]].append(c)

    resultado = []
    tiendas_activas = list(por_tienda.keys())
    random.shuffle(tiendas_activas)
    indice = 0
    while True:
        agregado = False
        for t in tiendas_activas:
            if indice < len(por_tienda[t]):
                resultado.append(por_tienda[t][indice])
                agregado = True
        if not agregado:
            break
        indice += 1

    return resultado


def scrapear_otras_tiendas_meepa(sesion_meepa, rutas):
    rutas_sin_amz = {k: v for k, v in rutas.items() if "amazon" not in k}
    otras = [
        r for r in rutas_sin_amz.keys() if r not in TIENDAS_ALTA_CONVERSION
    ]
    random.shuffle(otras)
    orden_rutas = [
        r for r in TIENDAS_ALTA_CONVERSION if r in rutas_sin_amz
    ] + otras

    candidatos = []

    for ruta in orden_rutas[:19]:
        nombre_tienda = rutas_sin_amz[ruta]
        for pagina in range(1, 3):
            try:
                resp = sesion_meepa.get(
                    f"{URL_MEEPA_BASE}{ruta}?{QUERY_PMH}&page={pagina}",
                    timeout=10,
                )
                if 'name="username"' in resp.text:
                    return None
                soup = BeautifulSoup(resp.text, "html.parser")
                items = extraer_tarjetas_pagina(
                    soup, nombre_tienda, es_pmh=True
                )
                if not items:
                    break
                candidatos.extend(items)
            except Exception:
                break
            time.sleep(random.uniform(0.12, 0.22))

        try:
            resp = sesion_meepa.get(
                f"{URL_MEEPA_BASE}{ruta}?{QUERY_TOP}&page=1", timeout=10
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            items = extraer_tarjetas_pagina(soup, nombre_tienda, es_pmh=False)
            candidatos.extend(items)
        except Exception:
            pass
        time.sleep(random.uniform(0.12, 0.22))

    for ruta in orden_rutas[19:55]:
        nombre_tienda = rutas_sin_amz[ruta]
        for query, es_pmh in [(QUERY_PMH, True), (QUERY_TOP, False)]:
            try:
                resp = sesion_meepa.get(
                    f"{URL_MEEPA_BASE}{ruta}?{query}", timeout=10
                )
                soup = BeautifulSoup(resp.text, "html.parser")
                items = extraer_tarjetas_pagina(
                    soup, nombre_tienda, es_pmh=es_pmh
                )
                candidatos.extend(items[:8])
            except Exception:
                continue
            time.sleep(random.uniform(0.12, 0.22))

    vistos_id = set()
    vistos_var = set()
    por_tienda = defaultdict(list)

    for c in candidatos:
        if (
            c["id_unico"] not in vistos_id
            and c["clave_variante"] not in vistos_var
        ):
            vistos_id.add(c["id_unico"])
            vistos_var.add(c["clave_variante"])
            por_tienda[c["tienda"]].append(c)

    resultado = []
    tiendas_activas = list(por_tienda.keys())
    random.shuffle(tiendas_activas)
    indice = 0
    while True:
        agregado = False
        for t in tiendas_activas:
            if indice < len(por_tienda[t]):
                resultado.append(por_tienda[t][indice])
                agregado = True
        if not agregado:
            break
        indice += 1

    return resultado


# ==========================================
# 6. PUBLICACIÓN EN TELEGRAM (BLINDADA)
# ==========================================
def publicar_en_telegram(
    chollo, precio_mostrar, verificado=True, etiqueta_tech=False, etiqueta_cupon=False
):
    titulo_seguro = html.escape(chollo["titulo"])
    if etiqueta_cupon or chollo.get("cupon"):
        cabecera = "🎟️ <b>CHOLLO AMAZON CON CUPÓN</b>"
    elif etiqueta_tech or chollo.get("es_tech"):
        cabecera = "💻 <b>CHOLLO TECNOLOGÍA / INFORMÁTICA</b>"
    elif chollo["es_pmh"]:
        cabecera = "📉 <b>PRECIO MÍNIMO HISTÓRICO</b>"
    else:
        cabecera = "🔥 <b>CHOLLO DESTACADO</b>"

    badge_pmh_extra = (
        "📉 <i>Precio Mínimo Histórico</i>\n"
        if (etiqueta_cupon or etiqueta_tech or chollo.get("es_tech"))
        and chollo["es_pmh"]
        else ""
    )
    detalle_cupon = (
        f" ({html.escape(chollo['texto_cupon'])})"
        if chollo.get("texto_cupon")
        else ""
    )
    badge_cupon = (
        f"✂️ <b>¡Marca la casilla del cupón en la ficha!{detalle_cupon}</b>\n"
        if (etiqueta_cupon or chollo.get("cupon"))
        else ""
    )

    p_act = f"{precio_mostrar:.2f}".replace(".", ",")
    sello = (
        " ✅ <i>(Precio verificado)</i>"
        if verificado
        else " ⚠️ <i>(Precio de referencia MEEPA; no verificado en tienda)</i>"
    )

    texto = (
        f"{cabecera} | 🏪 <b>{html.escape(chollo['tienda'])}</b>\n"
        f"{badge_pmh_extra}"
        f"{badge_cupon}\n"
        f"📦 <b>{titulo_seguro}</b>\n\n"
        f"💶 <b>{p_act}€</b>{sello}\n\n"
        f'🛒 <a href="{chollo["url"]}">Ver chollo en {html.escape(chollo["tienda"])}</a>'
    )

    if chollo["imagen"] and chollo["imagen"].startswith("http"):
        endpoint = (
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "photo": chollo["imagen"],
            "caption": texto,
            "parse_mode": "HTML",
        }
    else:
        endpoint = (
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        )
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": texto,
            "parse_mode": "HTML",
        }

    for intento in range(1, 4):
        try:
            resp = requests.post(endpoint, data=payload, timeout=20)

            if resp.status_code == 429:
                espera = resp.json().get("parameters", {}).get("retry_after", 10)
                print(f"⏳ Límite de Telegram alcanzado. Esperando {espera}s...")
                time.sleep(espera + 1)
                continue

            if not resp.ok:
                print(f"⚠️ Error Telegram: {resp.text}")
                if "sendPhoto" in endpoint and intento == 1:
                    endpoint = (
                        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                    )
                    payload = {
                        "chat_id": TELEGRAM_CHAT_ID,
                        "text": texto,
                        "parse_mode": "HTML",
                    }
                    continue
                return False

            return True

        except Exception as e:
            print(f"⚠️ Corte de red con Telegram (intento {intento}/3): {e}")
            time.sleep(4 * intento)

    return False


# ==========================================
# 7. ORQUESTADOR PRINCIPAL
# ==========================================
def main():
    faltan = [
        nombre for nombre, valor in (
            ("MEEPA_USER", MEEPA_USER),
            ("MEEPA_PASS", MEEPA_PASS),
            ("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN),
            ("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID),
        ) if not valor
    ]
    if faltan:
        raise RuntimeError(
            "Faltan variables de entorno: " + ", ".join(faltan)
        )
    historial = cargar_historial()
    sesion_meepa = crear_sesion_meepa()
    if sesion_meepa is None:
        return

    try:
        rutas = obtener_rutas_tiendas(sesion_meepa)
        if rutas is None:
            sesion_meepa = crear_sesion_meepa()
            if sesion_meepa is None:
                return
            rutas = obtener_rutas_tiendas(sesion_meepa) or {}

        # ------------------------------------------
        # BLOQUE 1: TOP 40 MEJORES DE AMAZON EN MEEPA
        # ------------------------------------------
        print("\n--- BLOQUE 1: TOP MEJORES DE AMAZON (MEEPA) ---")
        lista_amz = scrapear_amazon_meepa(sesion_meepa) or []
        nuevos_amz = [
            c
            for c in lista_amz
            if c["id_unico"] not in historial
            and c["clave_variante"] not in historial
        ]
        print(
            f"📊 Candidatos Amazon en Meepa: {len(lista_amz)} | Nuevos:"
            f" {len(nuevos_amz)}"
        )

        enviados_amz = 0
        for chollo in nuevos_amz:
            if enviados_amz >= MAX_AMAZON_POR_PASADA:
                break

            if chollo["asin"]:
                es_valido, precio_final, verif = inspeccionar_ficha_amazon_movil(
                    chollo["asin"], chollo["precio_meepa"]
                )
            else:
                es_valido, precio_final, verif = (
                    True,
                    chollo["precio_meepa"],
                    False,
                )

            if not es_valido:
                continue

            if publicar_en_telegram(chollo, precio_final, verificado=verif):
                historial.add(chollo["id_unico"])
                historial.add(chollo["clave_variante"])
                enviados_amz += 1
                print(
                    f"🚀 [Amazon {enviados_amz}/{MAX_AMAZON_POR_PASADA}]"
                    f" {precio_final:.2f}€ | {chollo['titulo'][:45]}..."
                )
                time.sleep(PAUSA_TELEGRAM_SEGUNDOS)
        guardar_historial(historial)

        # ------------------------------------------
        # BLOQUE 2: CUPONES DE AMAZON (SECCIÓN CUPONES AMAZON.ES)
        # ------------------------------------------
        print("\n--- BLOQUE 2: CUPONES DE AMAZON (AMAZON.ES) ---")
        lista_cupones = scrapear_amazon_cupones_playwright()
        nuevos_cupones = [
            c
            for c in lista_cupones
            if c["id_unico"] not in historial
            and f"MEEPA_{c['asin']}" not in historial
            and c["clave_variante"] not in historial
        ]
        print(
            f"📊 Candidatos Cupones Amazon extraídos: {len(lista_cupones)} |"
            f" Nuevos: {len(nuevos_cupones)}"
        )

        enviados_cupones = 0
        for chollo in nuevos_cupones:
            if enviados_cupones >= MAX_CUPONES_AMAZON_POR_PASADA:
                break

            if publicar_en_telegram(
                chollo,
                chollo["precio_meepa"],
                verificado=True,
                etiqueta_cupon=True,
            ):
                historial.add(chollo["id_unico"])
                historial.add(f"MEEPA_{chollo['asin']}")
                historial.add(chollo["clave_variante"])
                enviados_cupones += 1
                print(
                    f"🎟️ [Cupón Amazon"
                    f" {enviados_cupones}/{len(nuevos_cupones)}]"
                    f" {chollo['precio_meepa']:.2f}€ |"
                    f" {chollo['titulo'][:42]}..."
                )
                time.sleep(PAUSA_TELEGRAM_SEGUNDOS)
        guardar_historial(historial)

        # ------------------------------------------
        # BLOQUE 3: ELECTRÓNICA, INFORMÁTICA, FOTO Y PORTÁTILES
        # ------------------------------------------
        print(
            "\n--- BLOQUE 3: ELECTRÓNICA, INFORMÁTICA, CÁMARAS Y PORTÁTILES ---"
        )
        lista_tech = scrapear_tecnologia_meepa(sesion_meepa, rutas) or []
        nuevos_tech = [
            c
            for c in lista_tech
            if c["id_unico"] not in historial
            and c["clave_variante"] not in historial
        ]
        print(
            f"📊 Candidatos Tecnología/Informática: {len(lista_tech)} |"
            f" Nuevos: {len(nuevos_tech)}"
        )

        enviados_tech = 0
        for inicio in range(0, len(nuevos_tech), 30):
            if enviados_tech >= MAX_TECH_POR_PASADA:
                break

            lote_tech = nuevos_tech[inicio : inicio + 30]
            with ThreadPoolExecutor(max_workers=12) as executor:
                futuros = [
                    executor.submit(verificar_chollo_en_tienda, c)
                    for c in lote_tech
                ]

                for futuro in as_completed(futuros):
                    if enviados_tech >= MAX_TECH_POR_PASADA:
                        break

                    chollo, es_valido, precio_tienda = futuro.result()
                    if (
                        not es_valido
                        or chollo["clave_variante"] in historial
                    ):
                        continue

                    if publicar_en_telegram(
                        chollo,
                        precio_tienda,
                        verificado=chollo.get("_precio_verificado", False),
                        etiqueta_tech=True,
                    ):
                        historial.add(chollo["id_unico"])
                        historial.add(chollo["clave_variante"])
                        enviados_tech += 1
                        print(
                            f"💻 [Tech {enviados_tech}/{MAX_TECH_POR_PASADA}]"
                            f" [{chollo['tienda']}] {precio_tienda:.2f}€ |"
                            f" {chollo['titulo'][:42]}..."
                        )
                        time.sleep(PAUSA_TELEGRAM_SEGUNDOS)
        guardar_historial(historial)

        # ------------------------------------------
        # BLOQUE 4: RESTO DE TIENDAS
        # ------------------------------------------
        print("\n--- BLOQUE 4: RESTO DE TIENDAS ---")
        lista_otras = scrapear_otras_tiendas_meepa(sesion_meepa, rutas) or []
        nuevos_otras = [
            c
            for c in lista_otras
            if c["id_unico"] not in historial
            and c["clave_variante"] not in historial
        ]
        print(f"📊 Candidatos otras tiendas a verificar: {len(nuevos_otras)}")

        enviados_otras = 0
        conteo_tienda = defaultdict(int)

        for inicio in range(0, len(nuevos_otras), 60):
            if enviados_otras >= MAX_OTRAS_TIENDAS_POR_PASADA:
                break

            lote = [
                c
                for c in nuevos_otras[inicio : inicio + 60]
                if conteo_tienda[c["tienda"]] < MAX_POR_TIENDA_EN_PASADA
            ]
            if not lote:
                continue

            with ThreadPoolExecutor(max_workers=16) as executor:
                futuros = [
                    executor.submit(verificar_chollo_en_tienda, c)
                    for c in lote
                ]

                for futuro in as_completed(futuros):
                    if enviados_otras >= MAX_OTRAS_TIENDAS_POR_PASADA:
                        break

                    chollo, es_valido, precio_tienda = futuro.result()

                    if (
                        not es_valido
                        or conteo_tienda[chollo["tienda"]]
                        >= MAX_POR_TIENDA_EN_PASADA
                        or chollo["clave_variante"] in historial
                    ):
                        continue

                    if publicar_en_telegram(
                        chollo,
                        precio_tienda,
                        verificado=chollo.get("_precio_verificado", False),
                    ):
                        historial.add(chollo["id_unico"])
                        historial.add(chollo["clave_variante"])
                        conteo_tienda[chollo["tienda"]] += 1
                        enviados_otras += 1
                        print(
                            f"🚀 [Tienda"
                            f" {enviados_otras}/{MAX_OTRAS_TIENDAS_POR_PASADA}]"
                            f" [{chollo['tienda']}] {precio_tienda:.2f}€ |"
                            f" {chollo['titulo'][:42]}..."
                        )
                        time.sleep(PAUSA_TELEGRAM_SEGUNDOS)

        print(
            f"\n✅ Pasada completada. Publicados: {enviados_amz} Amazon +"
            f" {enviados_cupones} Cupones Amazon + {enviados_tech} Tecnología"
            f" + {enviados_otras} otras tiendas."
        )

    except Exception as e:
        print(f"❌ Error general: {e}")
        raise
    finally:
        guardar_historial(historial)


if __name__ == "__main__":
    main()