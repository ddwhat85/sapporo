"""
analyzer/product_analyzer.py
TrafficAI Engine 1.0 — Product Analyzer

네이버 쇼핑 API 키워드 검색으로 일본직구 상품 수집
소스: Naver Shopping API (NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 필요)
"""

from __future__ import annotations

import os
import re
import time
import logging
import hashlib
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
]

SEARCH_KEYWORDS = [
    {"query": "일본직구 드럭스토어", "category": "드럭스토어", "city": "일본"},
    {"query": "일본 화장품 직구", "category": "뷰티/스킨케어", "city": "일본"},
    {"query": "일본 과자 직구", "category": "식품/간식", "city": "일본"},
    {"query": "일본 직구 생활용품", "category": "생활용품", "city": "일본"},
    {"query": "삿포로 직구", "category": "드럭스토어", "city": "삿포로"},
]

SOURCE_STORES = {}

# ---------------------------------------------------------------------------
# 데이터 스키마
# ---------------------------------------------------------------------------

@dataclass
class ProductData:
    product_id: str
    store_key: str
    store_name: str
    product_name: str
    price: int
    original_price: int
    image_url: str
    product_url: str
    category: str
    tags: list = field(default_factory=list)
    review_count: int = 0
    review_score: float = 0.0
    city: str = "일본"
    raw_data: dict = field(default_factory=dict)

    @property
    def discount_rate(self) -> float:
        if self.original_price and self.original_price > self.price:
            return round((1 - self.price / self.original_price) * 100, 1)
        return 0.0

    def to_story_hint(self) -> dict:
        return {
            "product_name": self.product_name,
            "store_name": self.store_name,
            "city": self.city,
            "category": self.category,
            "price": self.price,
            "discount_rate": self.discount_rate,
            "review_score": self.review_score,
            "tags": self.tags,
        }

# ---------------------------------------------------------------------------
# HTTP 헬퍼
# ---------------------------------------------------------------------------

class FetchClient:
    def __init__(self, timeout=12, max_retries=3, min_delay=1.0, max_delay=3.0):
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_delay = min_delay
        self.max_delay = max_delay
        self._session = requests.Session()

    def _headers(self):
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,ja;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
        }

    def get(self, url):
        delay = self.min_delay
        for attempt in range(1, self.max_retries + 1):
            try:
                time.sleep(delay + random.uniform(0, 0.5))
                resp = self._session.get(url, headers=self._headers(), timeout=self.timeout, allow_redirects=True)
                resp.raise_for_status()
                return BeautifulSoup(resp.text, "html.parser")
            except requests.HTTPError as e:
                logger.warning(f"[HTTP {e.response.status_code}] {url} (attempt {attempt})")
                if e.response.status_code in (403, 404):
                    return None
            except requests.RequestException as e:
                logger.warning(f"[CONN ERR] {url}: {e} (attempt {attempt})")
            delay = min(delay * 2, 8.0)
        logger.error(f"[GIVE UP] {url} after {self.max_retries} retries")
        return None

    def fetch_og_image(self, url):
        if not url:
            return ""
        try:
            resp = self._session.get(url, headers=self._headers(), timeout=self.timeout, allow_redirects=True)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                return og["content"].strip()
            tw = soup.find("meta", attrs={"name": "twitter:image"})
            if tw and tw.get("content"):
                return tw["content"].strip()
        except Exception as e:
            logger.debug(f"[OG:IMAGE] {url}: {e}")
        return ""

# ---------------------------------------------------------------------------
# 오케스트레이터
# ---------------------------------------------------------------------------

class ProductAnalyzer:
    """
    네이버 쇼핑 API 키워드 검색으로 일본직구 상품 수집 및 점수화.

    환경변수:
    NAVER_CLIENT_ID : Naver 오픈 API 클라이언트 ID
    NAVER_CLIENT_SECRET : Naver 오픈 API 시크릿
    PA_MIN_SCORE : 최소 리뷰 점수 필터 (기본 60.0)
    PA_MIN_PRICE : 최소 가격 필터 KRW (기본 0)
    PA_MAX_PRICE : 최대 가격 필터 KRW (기본 500000)
    """

    NAVER_API_URL = "https://openapi.naver.com/v1/search/shop.json"

    def __init__(self):
        self.min_score = float(os.getenv("PA_MIN_SCORE", "60.0"))
        self.min_price = int(os.getenv("PA_MIN_PRICE", "0"))
        self.max_price = int(os.getenv("PA_MAX_PRICE", "500000"))
        self._client = FetchClient()

    def fetch_all(self, per_store=20):
        all_products = []
        seen_ids = set()
        naver_id = os.getenv("NAVER_CLIENT_ID", "")
        naver_secret = os.getenv("NAVER_CLIENT_SECRET", "")
        if not (naver_id and naver_secret):
            logger.error("[ANALYZER] NAVER_CLIENT_ID/SECRET 미설정. 수집 불가.")
            return []
        for kw in SEARCH_KEYWORDS:
            try:
                logger.info(f"[ANALYZER] 키워드 검색: {kw['query']}")
                batch = self._fetch_by_keyword(
                    query=kw["query"], category=kw["category"], city=kw["city"],
                    limit=per_store, naver_id=naver_id, naver_secret=naver_secret,
                    store_filter=kw.get("store_filter", ""),
                )
                new = [p for p in batch if p.product_id not in seen_ids]
                seen_ids.update(p.product_id for p in new)
                all_products.extend(new)
                logger.info(f"[ANALYZER] '{kw['query']}' → {len(new)}개")
            except Exception as e:
                logger.error(f"[ANALYZER] '{kw['query']}' 오류: {e}")
        logger.info(f"[ANALYZER] 총 {len(all_products)}개 수집")
        self._enrich_images(all_products)
        return all_products

    def _fetch_by_keyword(self, query, category, city, limit, naver_id, naver_secret, store_filter=""):
        params = {"query": query, "display": min(limit, 100), "sort": "date"}
        headers = {"X-Naver-Client-Id": naver_id, "X-Naver-Client-Secret": naver_secret}
        try:
            resp = requests.get(self.NAVER_API_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            logger.info(f"[NAVER API] '{query}': {len(items)}개 수신")
        except Exception as e:
            logger.warning(f"[NAVER API] '{query}': {e}")
            return []
        products = []
        for item in items:
            if store_filter:
                mall = item.get("mallName", "").lower()
                link = item.get("link", "").lower()
                if store_filter not in mall and store_filter not in link:
                    continue
            name_clean = re.sub(r"<[^>]+>", "", item.get("title", "")).strip()
            if not name_clean:
                continue
            price = int(re.sub(r"\D", "", item.get("lprice", "0")) or 0)
            h_price = int(re.sub(r"\D", "", item.get("hprice", "0")) or 0)
            raw_id = item.get("productId") or item.get("link", name_clean)[:20]
            pid = "KW-" + hashlib.md5(f"{query}:{raw_id}".encode()).hexdigest()[:8]
            products.append(ProductData(
                product_id=pid, store_key="naver_search",
                store_name=item.get("mallName", "네이버쇼핑"),
                product_name=name_clean, price=price,
                original_price=h_price if h_price > price else price,
                image_url=item.get("image", ""), product_url=item.get("link", ""),
                category=category, tags=[item.get("brand", "")],
                review_score=75.0, city=city, raw_data=item,
            ))
        return products

    def fetch_store(self, store_key, limit=20):
        raise ValueError(f"store_key 검색은 지원되지 않음. fetch_all() 사용.")

    def _enrich_images(self, products):
        for p in products:
            if not p.image_url and p.product_url:
                logger.debug(f"[OG:IMAGE] {p.product_name[:30]} → og:image 조회 중")
                og_url = self._client.fetch_og_image(p.product_url)
                if og_url:
                    p.image_url = og_url

    def pick_best(self, products, n=4, exclude_ids=None):
        if exclude_ids is None:
            exclude_ids = set()
        filtered = []
        for p in products:
            if p.product_id in exclude_ids:
                continue
            if not p.product_name:
                continue
            if not (self.min_price <= p.price <= self.max_price):
                continue
            if p.review_score < self.min_score:
                continue
            filtered.append(p)
        logger.info(f"[pick_best] {len(products)}개 중 {len(filtered)}개 통과")

        PRIORITY_STORES = {"sapporofactory", "portablejapan", "dunkjapan", "geminijapan"}

        def _score(p):
            from datetime import datetime as _dt
            base = p.review_score
            url_lower = p.product_url.lower()
            store_lower = p.store_name.lower()
            if any(s in url_lower or s in store_lower for s in PRIORITY_STORES):
                base += 60.0
            hour = _dt.now().hour
            if 14 <= hour <= 16:
                cat = p.category.lower()
                name = p.product_name.lower()
                if any(k in cat for k in ("식품", "간식", "food", "스낵", "과자")):
                    base += 30.0
                if any(k in name for k in ("과자", "초콜릿", "구미", "스낵", "캔디", "쿠키", "젤리", "킷캣", "포키", "칩")):
                    base += 25.0
            return base

        filtered.sort(key=_score, reverse=True)
        selected = []
        used_stores = set()
        for p in filtered:
            if len(selected) >= n:
                break
            if p.store_key not in used_stores:
                selected.append(p)
                used_stores.add(p.store_key)
        for p in filtered:
            if len(selected) >= n:
                break
            if p not in selected:
                selected.append(p)
        logger.info(f"[pick_best] 선별 완료: {len(selected)}개")
        return selected[:n]
