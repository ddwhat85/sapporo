"""
daily_runner.py
TrafficAI Engine 1.0 — 일일 발행 오케스트레이터

실행 방법:
python daily_runner.py # 오늘 4개 슬롯 모두 발행
python daily_runner.py --dry-run # 실제 전송 없이 콘텐츠 확인만
python daily_runner.py --slot 18:20 # 특정 슬롯만 발행
python daily_runner.py --store sapporofactory # 특정 스토어만 사용
python daily_runner.py --limit 2 # 최대 2개만 발행

환경변수:
OPENAI_API_KEY or ANTHROPIC_API_KEY (AI 생성용)
NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (선택 — Naver API 우선 사용)
PA_MIN_SCORE / PA_MIN_PRICE / PA_MAX_PRICE (상품 필터)
MAKE_WEBHOOK_URL (기본값 내장)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 경로 설정 (이 파일이 repo 루트에 위치)
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.resolve()
sys.path.insert(0, str(ROOT))

from analyzer.product_analyzer import ProductAnalyzer, ProductData
from writer.content_writer import ContentWriter, StoryPacket
from publisher.make_connector import (
    MakeConnector,
    PublishPacket,
    FAQItem,
    PUBLISH_SLOTS,
    pick_target_time,
)

# ---------------------------------------------------------------------------
# 로거
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / f"run_{date.today().isoformat()}.log",
                            encoding="utf-8"),
    ],
)
logger = logging.getLogger("daily_runner")

# ---------------------------------------------------------------------------
# 발행 이력 (중복 방지)
# ---------------------------------------------------------------------------
STATE_PATH = ROOT / "state" / "published_ids.json"

def load_published_ids() -> set[str]:
    if not STATE_PATH.exists():
        return set()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        today_str = date.today().isoformat()
        return set(data.get(today_str, []))
    except Exception as e:
        logger.warning(f"state 로드 실패: {e}")
        return set()

def save_published_id(product_id: str) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    today_str = date.today().isoformat()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
        day_list = data.get(today_str, [])
        if product_id not in day_list:
            day_list.append(product_id)
        data[today_str] = day_list
        cutoff = date.today().toordinal() - 30
        data = {k: v for k, v in data.items()
                if date.fromisoformat(k).toordinal() >= cutoff}
        STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"state 저장 실패: {e}")

# ---------------------------------------------------------------------------
# 도시별 대표 상황 힌트 풀
# ---------------------------------------------------------------------------
CITY_SITUATIONS = {
    "삿포로": [
        "삿포로 다누키코지 쇼핑 아케이드를 걷다가",
        "삿포로역 지하 아피아몰에서",
        "오도리 공원 근처 드럭스토어 들렀다가",
        "홋카이도 대학 캠퍼스 근처 편의점에서",
    ],
    "오사카": [
        "도톤보리 돌아다니다 들린 마쓰키요에서",
        "신사이바시 쇼핑 스트리트 걷다가",
        "우메다 한큐 지하 푸드홀 구경하다가",
        "난바 파크스 근처 드럭스토어에서",
    ],
    "도쿄": [
        "신주쿠 이세탄 지하 식품관 구경하다가",
        "아키하바라 전자거리 걷다가 우연히",
        "하라주쿠 다케시타도리에서",
        "시부야 109 근처 돌아다니다가",
    ],
    "교토": [
        "기온 거리 산책하다가 들린 편의점에서",
        "아라시야마 죽림 길 걸은 후 카페에서",
        "니시키 시장 구경하다가",
        "후시미이나리 다녀온 후 역 근처에서",
    ],
    "일본": [
        "일본 드럭스토어에서",
        "돈키호테 구경하다가",
        "마쓰모토키요시 들렀다가",
        "코코카라파인에서",
        "선드럭 들렀다가",
    ],
}

EMOTIONS = [
    "설레는", "여유롭고 느긋한", "새로운 걸 발견한 기쁨",
    "신나는", "여행 마지막 날 아쉬운", "뭔가 건져야 할 것 같은",
]

CATEGORY_FEATURES = {
    "뷰티/스킨케어": ["피부 보습", "일본 현지 인기", "드럭스토어 베스트셀러", "자외선 차단"],
    "드럭스토어": ["드럭스토어 인기템", "일본인도 즐겨 쓰는", "마쓰키요 베스트", "가성비 좋은"],
    "식품/간식": ["일본 한정 맛", "현지인도 즐겨 먹는", "일본 여행 하면 꼭", "한국에서 못 사는"],
    "헤어케어": ["일본 살롱 추천", "손상 모발 케어", "향이 은은한", "가성비 좋은"],
    "일반상품": ["일본에서만 파는", "현지 인기 상품", "돈키호테 발견", "가성비 최고"],
}

def product_to_story_packet(product: ProductData) -> StoryPacket:
    city = product.city or "일본"
    situations = CITY_SITUATIONS.get(city, CITY_SITUATIONS["일본"])
    situation = random.choice(situations)
    emotion = random.choice(EMOTIONS)
    name_short = product.product_name[:12].strip()
    city_label = city if city != "일본" else "일본 여행"
    story_theme = f"{city_label} 중 발견한 {name_short}"
    features = CATEGORY_FEATURES.get(product.category, CATEGORY_FEATURES["일반상품"])
    if product.discount_rate >= 10:
        features = [f"{int(product.discount_rate)}% 할인 중"] + features
    return StoryPacket(
        product_id=product.product_id,
        product_name=product.product_name,
        store_name=product.store_name,
        product_url=product.product_url,
        image_url=product.image_url,
        city=city,
        situation=situation,
        emotion=emotion,
        story_theme=story_theme,
        product_features=features[:4],
        price_krw=product.price if product.price > 0 else None,
    )

def make_publish_packet(product, content_output, target_time, generation_attempt=1):
    faq_items = []
    for faq in content_output.faq_data:
        if isinstance(faq, dict):
            q = faq.get("question", "")
            a = faq.get("answer", "")
        elif hasattr(faq, "question"):
            q, a = faq.question, faq.answer
        else:
            continue
        if q and a:
            faq_items.append(FAQItem(question=q, answer=a))
    return PublishPacket(
        product_id=product.product_id,
        store_name=product.store_name,
        product_name=product.product_name,
        image_url=product.image_url,
        product_url=product.product_url,
        content=content_output.content,
        target_time=target_time,
        faq_data=faq_items,
        review_score=product.review_score,
        story_theme=f"{product.city} | {content_output.structure_variant}",
        generation_attempt=generation_attempt,
    )

def run_single(product, target_time, writer, connector, dry_run=False):
    logger.info(f"▶ [{product.store_name}] {product.product_name[:30]} @ {target_time}")
    story = product_to_story_packet(product)
    try:
        output = writer.generate(story)
        logger.info(f" 콘텐츠 생성 OK — 구조={output.structure_variant} | 톤={output.tone_variant}")
    except RuntimeError as e:
        logger.error(f" 콘텐츠 생성 실패: {e}")
        return {"success": False, "pipeline_id": "", "error": str(e)}
    packet = make_publish_packet(product, output, target_time)
    if dry_run:
        logger.info(f" [DRY RUN] 전송 건너뜀. pipeline_id={packet.pipeline_id}")
        return {"success": True, "pipeline_id": packet.pipeline_id, "error": ""}
    result = connector.send(packet)
    if result["success"]:
        logger.info(f" ✅ 전송 성공 — HTTP {result['status_code']}")
        return {"success": True, "pipeline_id": packet.pipeline_id, "error": ""}
    else:
        logger.error(f" ❌ 전송 실패 — {result['response']}")
        return {"success": False, "pipeline_id": packet.pipeline_id, "error": result["response"]}

def run_daily(slots=None, store_filter=None, per_store_fetch=20, dry_run=False, limit=None):
    slots = slots or PUBLISH_SLOTS
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "state").mkdir(exist_ok=True)
    logger.info("=" * 60)
    logger.info(f"TrafficAI Engine 일일 발행 시작 — {date.today()}")
    logger.info(f"슬롯: {slots} | dry_run={dry_run} | store={store_filter or '전체'}")
    logger.info("=" * 60)
    analyzer = ProductAnalyzer()
    if store_filter:
        products = analyzer.fetch_store(store_filter, limit=per_store_fetch)
    else:
        products = analyzer.fetch_all(per_store=per_store_fetch)
    if not products:
        logger.error("수집된 상품 없음. 종료.")
        return
    published_ids = load_published_ids()
    n_needed = min(len(slots), limit or len(slots))
    best = analyzer.pick_best(products, n=n_needed, exclude_ids=published_ids)
    if not best:
        logger.warning("선별된 상품 없음. 종료.")
        return
    logger.info(f"선별 완료: {len(best)}개 상품")
    writer = ContentWriter()
    connector = MakeConnector()
    summary = []
    for i, (slot, product) in enumerate(zip(slots, best)):
        logger.info(f"\n[{i+1}/{len(best)}] 슬롯 {slot}")
        result = run_single(product=product, target_time=slot, writer=writer, connector=connector, dry_run=dry_run)
        summary.append({
            "slot": slot, "product_id": product.product_id,
            "product_name": product.product_name, "store": product.store_name,
            "success": result["success"], "pipeline_id": result.get("pipeline_id", ""),
            "error": result.get("error", ""),
        })
        if result["success"] and not dry_run:
            save_published_id(product.product_id)
        if i < len(best) - 1:
            delay = random.uniform(3.0, 7.0)
            logger.info(f" 다음 발행까지 {delay:.1f}초 대기...")
            time.sleep(delay)
    logger.info("\n" + "=" * 60)
    ok_count = sum(1 for r in summary if r["success"])
    for r in summary:
        status = "✅" if r["success"] else "❌"
        logger.info(f" {status} {r['slot']} | {r['store']} | {r['product_name'][:25]}" + (f" | {r['error']}" if r["error"] else ""))
    logger.info(f"\n성공: {ok_count}/{len(summary)}")
    summary_path = ROOT / "logs" / f"summary_{date.today().isoformat()}.json"
    try:
        existing = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else []
        existing.extend(summary)
        summary_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"요약 저장 실패: {e}")

def parse_args():
    parser = argparse.ArgumentParser(description="TrafficAI Engine — 일일 발행 오케스트레이터")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--slot", type=str, default=None)
    parser.add_argument("--store", type=str, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--per-store", type=int, default=20)
    return parser.parse_args()

if __name__ == "__main__":
    args = parse_args()
    slots = [args.slot] if args.slot else None
    run_daily(slots=slots, store_filter=args.store, per_store_fetch=args.per_store, dry_run=args.dry_run, limit=args.limit)
