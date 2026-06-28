"""
publisher/make_connector.py
TrafficAI Engine 1.0 â Make.com Webhook Connector

Data flow: Python Engine -> Make.com Webhook -> Google Sheets log + Buffer -> Threads
"""

import os
import re
import json
import logging
import time
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

PUBLISH_SLOTS = ["08:10", "12:05", "15:05", "18:20", "22:10"]


@dataclass
class FAQItem:
    question: str
    answer: str


@dataclass
class PublishPacket:
    product_id: str
    store_name: str
    product_name: str
    image_url: str
    product_url: str
    content: str
    target_time: str
    faq_data: list = field(default_factory=list)
    review_score: float = 0.0
    story_theme: str = ""
    first_comment: str = ""
    generation_attempt: int = 1
    pipeline_id: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y%m%d-%H%M%S"))
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def __post_init__(self):
        self.first_comment = "https://5makase.com"

    def _next_kst_datetime(self) -> str:
        """항상 미래인 target_time의 KST ISO8601 datetime 반환 (GitHub Actions 지연 대응)."""
        from datetime import timezone, timedelta as td
        KST = timezone(td(hours=9))
        now = datetime.now(KST)
        h, m = map(int, self.target_time.split(':'))
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now + td(minutes=3):
            target += td(days=1)
        return target.strftime("%Y-%m-%dT%H:%M:%S+09:00")

    def to_webhook_payload(self) -> dict:
        return {
            "pipeline_id":        self.pipeline_id,
            "product_id":         self.product_id,
            "store_name":         self.store_name,
            "product_name":       self.product_name,
            "image_url":          self.image_url,
            "product_url":        self.product_url,
            "first_comment":      self.first_comment,
            "content":            self.content + "\n5makase.com",
            "target_time":        self.target_time,
            "target_datetime":    self._next_kst_datetime(),
            "faq_data":           [asdict(f) for f in self.faq_data],
            "review_score":       self.review_score,
            "story_theme":        self.story_theme,
            "generation_attempt": self.generation_attempt,
            "created_at":         self.created_at,
        }


class ContentSafetyChecker:
    _URL_RE      = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
    _HASHTAG_RE  = re.compile(r'#\w+')
    _EMOJI_RE    = re.compile(
        "[\U00010000-\U0010ffff"
        "\U0001F300-\U0001F9FF"
        "\u2600-\u27BF]+",
        flags=re.UNICODE
    )

    def check(self, content: str):
        violations = []
        if self._URL_RE.search(content):
            violations.append("URL in content")
        if self._HASHTAG_RE.search(content):
            violations.append("hashtag in content")
        emojis = self._EMOJI_RE.findall(content)
        if len(emojis) > 1:
            violations.append(f"too many emojis: {len(emojis)}")
        return len(violations) == 0, violations


class MakeConnector:
    DEFAULT_WEBHOOK = "https://hook.eu1.make.com/km29aysbsbv1w8y2ll9lsmzjoqviy9dm"

    def __init__(self):
        self.webhook_url = os.getenv("MAKE_WEBHOOK_URL", self.DEFAULT_WEBHOOK)
        self.timeout = int(os.getenv("MAKE_TIMEOUT_SEC", "10"))
        self._checker = ContentSafetyChecker()

        retry = Retry(total=int(os.getenv("MAKE_MAX_RETRIES", "3")),
                      backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        self._session = requests.Session()
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json; charset=utf-8"}
        token = os.getenv("MAKE_SECRET_TOKEN", "")
        if token:
            h["X-Make-Token"] = token
        return h

    def send(self, packet: PublishPacket) -> dict:
        is_safe, violations = self._checker.check(packet.content)
        if not is_safe:
            logger.error(f"[BLOCKED] {violations}")
            return {"success": False, "status_code": 0,
                    "response": "Content safety check failed", "violations": violations}

        if packet.target_time not in PUBLISH_SLOTS:
            logger.warning(f"target_time '{packet.target_time}' not in recommended slots")

        payload_bytes = json.dumps(
            packet.to_webhook_payload(), ensure_ascii=False
        ).encode("utf-8")

        logger.info(f"[SEND] id={packet.product_id} | time={packet.target_time}")
        try:
            resp = self._session.post(
                self.webhook_url,
                data=payload_bytes,
                headers=self._headers(),
                timeout=self.timeout,
            )
            resp.raise_for_status()
            logger.info(f"[OK] HTTP {resp.status_code}")
            return {"success": True, "status_code": resp.status_code,
                    "response": resp.text, "violations": []}
        except requests.HTTPError as e:
            code = e.response.status_code if e.response else 0
            logger.error(f"[HTTP {code}] {e}")
            return {"success": False, "status_code": code,
                    "response": str(e), "violations": []}
        except requests.ConnectionError as e:
            logger.error(f"[CONN ERR] {e}")
            return {"success": False, "status_code": 0,
                    "response": str(e), "violations": []}
        except requests.Timeout:
            return {"success": False, "status_code": 0,
                    "response": "Timeout", "violations": []}
        except Exception as e:
            logger.exception(f"[UNKNOWN] {e}")
            return {"success": False, "status_code": 0,
                    "response": str(e), "violations": []}


def pick_target_time(prefer: Optional[str] = None) -> str:
    if prefer and prefer in PUBLISH_SLOTS:
        return prefer
    now = datetime.now().strftime("%H:%M")
    for slot in PUBLISH_SLOTS:
        if slot > now:
            return slot
    return PUBLISH_SLOTS[0]


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    connector = MakeConnector()
    sample = PublishPacket(
        product_id   = "JP-2024-001",
        store_name   = "test-store",
        product_name = "test product",
        image_url    = "https://example.com/image.jpg",
        product_url  = "https://example.com/product",
        content      = "íì¤í¸ ì½íì¸ . ì¼ë³¸ ì¬í ê°ë©´ ê¼­ ì¬ë´ì¼ í  ìì´í ìì´?",
        target_time  = pick_target_time(),
    )
    result = connector.send(sample)
    print(json.dumps(result, ensure_ascii=False, indent=2))
