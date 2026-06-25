"""
writer/content_writer.py
TrafficAI Engine 1.0 — AI Content Writer v2
"""

import os
import re
import json
import random
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass
class StoryPacket:
    product_id: str
    product_name: str
    store_name: str
    product_url: str
    image_url: str
    city: str
    situation: str
    emotion: str
    story_theme: str
    product_features: list = field(default_factory=list)
    price_krw: Optional[int] = None

@dataclass
class ContentOutput:
    content: str
    first_comment: str
    faq_data: list
    tone_variant: str
    structure_variant: str
    raw_prompt: str = ""

FOLLOWER_TERMS = ["스친이들", "치니덜", "스치나들", "치니"]

STRUCTURE_VARIANTS = {
    "situation_first": {"label": "상황 → 발견 → 특징 → 질문", "hint": "어디 갔다가 발견했는지 짧게 쓰고, 마지막은 팔로워한테 묻는 질문으로 끝."},
    "price_shock": {"label": "가격 놀람 → 설명 → 질문", "hint": "가격이 얼마나 싼지/좋은지 먼저 치고, 팔로워들 여기 아는지 묻어봐."},
    "contrast": {"label": "별기대없었는데 반전 → 질문", "hint": "처음엔 그냥 지나치려 했는데 사게 됨다는 흐름. 마지막은 이런거 본 적 있는지 경험 유도."},
    "recommendation": {"label": "강추 공유형 → 질문", "hint": "일본 가면 이거 꼭 사야 한다는 강추 톤. 팔로워들도 챙겨갔는지 묻어봐."},
    "honest_confession": {"label": "솔직 고백 → 상품 구원 → 공감 유도", "hint": "팔로워들아... 솔직하게 말할게 식으로 시작. '나만 이래?' 또는 '써본 사람 있어?' 식의 공감 유도."},
}

TONE_VARIANTS = {
    "ddwhat_basic": {"label": "ddwhat 기본체", "instruction": "ddwhat1985 실제 말투. 짧은 문장 줄바꾸. '스친이들' 또는 '치니덜' 호칭. ~있어, ~팅어, ~있음, ~아는 사람? 종결어미. 가족/아기 언급 절대 금지."},
    "ddwhat_excited": {"label": "ddwhat 흥분체", "instruction": "진짜 좋아서 흥분된 상태. '진짜' 2~3번 반복. '흥입했다', '미쳤다' 식으로 과장. 감성 과잇 표현 금지."},
    "dior_chill": {"label": "dior 여유체 + 알고리즘 썬", "instruction": "dior8524 말투. '..' 줄임표로 말 흔리기. '컨니덜' 또는 '스치나들' 호칭. '넘나', '넘', '짱이지', '~아님?', '~있었뉅?' 사용."},
    "dior_info": {"label": "dior 정보공유체", "instruction": "dior8524 정보 공유 스타일. '★★' 또는 감정 훅으로 시작. 상품 특징을 줄마다 나열. '좋앙!', '맛나더라' 표현. 마지막 질문."},
    "honest_confession": {"label": "얄직 고백체", "instruction": "yoonseul_ys 바이럴 구조 차용. '스치나들...' 또는 '치니덜...' + 감정 훅. 고백 선언 후 마지막: '나만 이래?', '써본 사람?', '공감되면 댓글 ぅ' 식 참여 유도."},
}

PHONE_TYPO_EXAMPLES = [
    ("엄청", "업청"), ("엄청나", "업청나"), ("너무", "넘"), ("너무나", "넘나"),
    ("팔아", "팔어"), ("있어", "있응"), ("사고있음", "사구있음"), ("좋아", "좋앙"),
    ("있었냐", "있었뉅"), ("눈떴었는데", "눈딯는데"), ("솔직히", "얄직이"),
    ("진짜로", "진짜루"), ("먹었는데", "먹은듯"), ("아는사람", "아는사람?う"),
]

OPENING_HOOKS = [
    "오늘 {city} 놀러왔는데", "지난번에 {city} 갔을때",
    "{city} 갔다가 {situation}에서", "오늘 {situation} 들렁는데",
    "{city} 여행중에 {situation} 들렁는데", "진짜 {city} 가면",
    "{situation} 갔다가 발견한건데", "{city} {situation}..오지마..",
    "스친이들 {city} 가면 꼭", "{city} 알고리즘이 자꾸 보여주는거야..",
    "새벽에 눈딯는데 {city} {situation}이 뜨는거야..",
    "스치나들...오늘 얄직하게 말할게", "진짜 {city} {situation} 다들 알아?",
]


class BaseAIProvider(ABC):
    @abstractmethod
    def complete(self, system: str, user: str, max_tokens: int = 600) -> str: ...


class OpenAIProvider(BaseAIProvider):
    def __init__(self):
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError("pip install openai")
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY 환경변수 필요")
        self._client = OpenAI(api_key=api_key)
        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

    def complete(self, system, user, max_tokens=600):
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens, temperature=0.93,
        )
        return resp.choices[0].message.content.strip()


class ClaudeProvider(BaseAIProvider):
    def __init__(self):
        try:
            import anthropic
        except ImportError:
            raise ImportError("pip install anthropic")
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY 환경변수 필요")
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = os.getenv("CLAUDE_MODEL", "claude-opus-4-8")

    def complete(self, system, user, max_tokens=600):
        import anthropic
        msg = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text.strip()


def get_ai_provider() -> BaseAIProvider:
    provider = os.getenv("AI_PROVIDER", "openai").lower()
    if provider == "claude":
        return ClaudeProvider()
    return OpenAIProvider()


FIRST_COMMENT_TEMPLATES = [
    "사진 속 이 제품, 일본 직구로 구할 수 있어~ 5makase.com 한번 들러봐 👀",
    "이거 일본 직구 궁금하면 → 5makase.com 에서 찾아봐! 일본 직구 전문이야",
    "직구 링크 알고 싶으면 → www.5makase.com (일본 직구 전문 사이트)",
    "이 상품 일본에서 직접 오는 거야~ 5makase.com 가면 더 많아",
    "일본 직구 더 보고 싶으면 → 5makase.com 북마크 해두",
]


class ContentWriter:
    _SAFETY_RULES = (
        "[절대 준수 규칙]\n"
        "1. 본문에 URL(http, www 등) 절대 포함 금지\n"
        "2. 해시태그(#으로 시작하는 단어) 절대 포함 금지\n"
        "3. 이모지는 본문 전체에서 최대 1개만 허용\n"
        "4. 본문 마지막 문장은 반드시 독자에게 던지는 자연스러운 질문으로 끝낼 것\n"
        "5. 광고·마케팅 문구 절대 금지: '구매', '할인', '이벤트', '지금 구입', '클릭', '링크' 절대 사용 금지\n"
        "6. 가격(숫자+원, 숫자+엔) 절대 포함 금지\n"
        "7. AI처럼 들리는 문장 금지: '~것 같지 않니?', '~어때세요?', '~해보세요', '~드립니다' 절대 금지\n"
        "8. 실제 사람이 핸드폰으로 빨리게 타이핑한 것쳄럼"
    )

    _POST_EXAMPLES = (
        "[실제 포스트 예시 1 — ddwhat1985 쇼핑 발견체]\n"
        "오늘 삿포로 놀러왔는데 니시마쁜야 있어\n"
        "여기가 유아옷 업청 싸게 팔어\n"
        "진짜 업청싸!\n"
        "스친이들 여기 아�J 사람? う\n\n"
        "[실제 포스트 예시 2 — dior8524 정보공유체]\n"
        "★★ 파운드 케이크 좋아하는 치니 있었뉅?\n"
        "이거 초코맛은 그냥 브라우니라고 보면 되는\n"
        "ꎾ덕 고급진 초코 맛\n"
        "한 번에 여러개 주문해서 냉동보관하기 좋앙!\n"
        "치니덜 이런거 먹어뵐뉅?\n\n"
        "[실제 포스트 예시 3 — 얄직고백체]\n"
        "스치나들...얄직히 말할게\n"
        "나 요즘 진짜 힙들었거든\n"
        "근데 이거 써보기 시작하면서 달라\n"
        "나만 이런 거 몰랐던 거야..?"
    )

    def __init__(self, provider=None):
        self._ai = provider or get_ai_provider()

    def _pick_variants(self):
        structure = random.choice(list(STRUCTURE_VARIANTS.values()))
        tone = random.choice(list(TONE_VARIANTS.values()))
        hook_tmpl = random.choice(OPENING_HOOKS)
        follower_term = random.choice(FOLLOWER_TERMS)
        return structure, tone, hook_tmpl, follower_term

    def _build_system_prompt(self, structure, tone, follower_term):
        typo_samples = random.sample(PHONE_TYPO_EXAMPLES, 2)
        typo_hint = ", ".join(f'"{a}" 대신 "{b}"처럼' for a, b in typo_samples)
        return (
            "너는 일본 직구 상품을 직접 써본 한국인 Threads 사용자야.\n"
            "팔로워들한테 자연스러운 일상 공유처럼 글을 써.\n\n"
            + self._POST_EXAMPLES + "\n\n---\n"
            + f'[이번 글 팔로워 호칭] → "{follower_term}"\n'
            + f'[이번 글 구조] {structure["label"]}: {structure["hint"]}\n'
            + f'[이번 글 톤] {tone["label"]}: {tone["instruction"]}\n'
            + f"[핸드폰 오타 규칙] 이번 글에 {typo_hint} 스타일 오타 포함.\n"
            + "[글 길이] 최대 8줄. 권장 6~8줄.\n\n"
            + self._SAFETY_RULES + "\n\n"
            + "출력 형식 (JSON만):\n"
            + '{"content": "Threads 본문 (줄바꾸은 \\n으로)", '
            + '"first_comment": "첫 댓글", '
            + '"faq": [{"question": "질문1", "answer": "답별1"}, {"question": "질문2", "answer": "답별2"}]}'
        )

    def _build_user_prompt(self, packet, hook_tmpl, follower_term):
        hook = hook_tmpl.format(city=packet.city, situation=packet.situation, emotion=packet.emotion)
        features = ", ".join(packet.product_features[:3]) if packet.product_features else "효과 좋음"
        return (
            "[스토리 컨텍스트]\n"
            f"여행지: {packet.city}\n상황: {packet.situation}\n"
            f"감성: {packet.emotion}\n스토리 테마: {packet.story_theme}\n"
            f'완료 오프닝 방향: "{hook}"\n팔로워 호칭: "{follower_term}"\n\n'
            "[상품 정보]\n"
            f"상품명: {packet.product_name}\n"
            f"주요 특징: {features}\n스토어: {packet.store_name}\n\n"
            "중요: product_url 절대 본문에 포함 금지.\n"
            "댓글에 '직구' 또는 '정보' 댓글 달아달라고 자연스러게 유도."
        )

    def _parse_output(self, raw):
        raw = raw.strip()
        # Remove markdown code fences using string operations (avoiding backtick conflicts)
        fence = '\x60\x60\x60'
        if raw.startswith(fence):
            first_nl = raw.find('\n')
            if first_nl != -1:
                raw = raw[first_nl + 1:]
            if raw.rstrip().endswith(fence):
                raw = raw.rstrip()[:-3].rstrip()
        return json.loads(raw)

    def _validate_content(self, content):
        issues = []
        if re.search(r'https?://\S+|www\.\S+', content):
            issues.append("URL 포함")
        if re.search(r'#\w+', content):
            issues.append("해시태그 포함")
        emoji_count = len(re.findall(
            r'[\U0001F300-\U0001F9FF\U0001FA00-\U0001FA9F✂-➰]', content
        ))
        if emoji_count > 1:
            issues.append(f"이모지 {emoji_count}개")
        for w in ['구매하세요', '클릭', '지금 구입', '이벤트', '할인코드']:
            if w in content:
                issues.append(f"광고 단어: {w}")
        return issues

    def generate(self, packet, max_attempts=3):
        for attempt in range(1, max_attempts + 1):
            structure, tone, hook_tmpl, follower_term = self._pick_variants()
            system = self._build_system_prompt(structure, tone, follower_term)
            user = self._build_user_prompt(packet, hook_tmpl, follower_term)
            logger.info(f"[GEN attempt={attempt}] structure={structure['label']} | tone={tone['label']}")
            try:
                raw = self._ai.complete(system, user, max_tokens=900)
                data = self._parse_output(raw)
            except Exception as e:
                logger.error(f"[GEN ERROR] {e}")
                continue
            content = data.get("content", "")
            issues = self._validate_content(content)
            if issues:
                logger.warning(f"[RULE VIOLATION] attempt={attempt} | {issues}")
                continue
            logger.info(f"[GEN OK] attempt={attempt}")
            return ContentOutput(
                content=content,
                first_comment=random.choice(FIRST_COMMENT_TEMPLATES),
                faq_data=data.get("faq", []),
                tone_variant=tone["label"],
                structure_variant=structure["label"],
                raw_prompt=user,
            )
        raise RuntimeError(f"콘텐츠 생성 실패: {max_attempts}회 모두 규칙 위반")
