"""
writer/content_writer.py
TrafficAI Engine 1.0 — AI Content Writer v3
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
    "situation_first": {
        "label": "상황 → 발견 → 특징 → 질문",
        "hint": "어디 갔다가 발견했는지 짧게 쓰고, 마지막은 팔로워한테 묻는 질문으로 끝."
    },
    "contrast": {
        "label": "별기대없었는데 반전 → 질문",
        "hint": "처음엔 그냥 지나치려 했는데 사게 됐다는 흐름. 마지막은 이런거 본 적 있는지 경험 유도."
    },
    "honest_confession": {
        "label": "솔직 고백 → 상품 구원 → 공감 유도",
        "hint": "팔로워들아... 솔직하게 말할게 식으로 시작. '나만 이래?' 또는 '써본 사람 있어?' 식의 공감 유도."
    },
    "debate_starter": {
        "label": "논쟁 훅 → 이유 제시 → 의견 묻기",
        "hint": "'이거 한국에 왜 없냐', '이거 보고 한국 XX 못 먹겠어됨' 식의 강한 의견으로 시작. 팔로워 의견 유도."
    },
    "slow_reveal": {
        "label": "감정/상황 먼저 → 상품 후반 공개 → 질문",
        "hint": "상품 이름을 바로 꺼내지 말고, 감정이나 상황을 2~3줄 쌓은 뒤 상품 공개. 독자가 '뭐야?' 하고 궁금하게 만들어."
    },
    "comparison": {
        "label": "비교/랭킹 → 결론 → 질문",
        "hint": "'일본 편의점 3군데 다 가봤는데', '이거 vs 저거 비교해봤어' 식 구조. 내가 직접 비교해본 결론 공유."
    },
    "hot_take": {
        "label": "강한 주장 → 근거 → 공감 유도",
        "hint": "'진짜로 이거 없으면 일본 여행 의미없음' 식 과감한 주장으로 시작. 이유를 짧게 대고 팔로워 동의 구하기."
    },
    "frustration_relief": {
        "label": "불만/문제 → 해결 발견 → 공유",
        "hint": "뭔가 불편하거나 아쉬웠던 점을 먼저 얘기하고, 이 상품이 그걸 해결해줬다는 흐름. 나처럼 이런 사람 있냐고 묻기."
    },
}

TONE_VARIANTS = {
    "ddwhat_basic": {
        "label": "ddwhat 기본체",
        "instruction": "ddwhat1985 실제 말투. 짧은 문장 줄바꾸. '스친이들' 또는 '치니덜' 호칭. ~있어, ~팅어, ~있음, ~아는 사람? 종결어미. 가족/아기 언급 절대 금지."
    },
    "ddwhat_excited": {
        "label": "ddwhat 흥분체",
        "instruction": "진짜 좋아서 흥분된 상태. '진짜' 2~3번 반복. '흥입했다', '미쳤다' 식으로 과장. 감성 과잉 표현 금지."
    },
    "dior_chill": {
        "label": "dior 여유체 + 알고리즘 썬",
        "instruction": "dior8524 말투. '..' 줄임표로 말 흘리기. '컨니덜' 또는 '스치나들' 호칭. '넘나', '넘', '짱이지', '~아님?', '~있었뉅?' 사용."
    },
    "dior_info": {
        "label": "dior 정보공유체",
        "instruction": "dior8524 정보 공유 스타일. '★★' 또는 감정 훅으로 시작. 상품 특징을 줄마다 나열. '좋앙!', '맛나더라' 표현. 마지막 질문."
    },
    "honest_confession": {
        "label": "얄직 고백체",
        "instruction": "yoonseul_ys 바이럴 구조 차용. '스치나들...' 또는 '치니덜...' + 감정 훅. 고백 선언 후 마지막: '나만 이래?', '써본 사람?', '공감되면 댓글 ぅ' 식 참여 유도."
    },
}

PHONE_TYPO_EXAMPLES = [
    ("엄청", "업청"), ("엄청나", "업청나"), ("너무", "넘"), ("너무나", "넘나"),
    ("팔아", "팔어"), ("있어", "있응"), ("사고있음", "사구있음"), ("좋아", "좋앙"),
    ("있었냐", "있었뉅"), ("눈떴었는데", "눈딯는데"), ("솔직히", "얄직이"),
    ("진짜로", "진짜루"), ("먹었는데", "먹은듯"), ("아는사람", "아는사람?う"),
]

OPENING_HOOKS_DISCOVERY = [
    "오늘 {city} 놀러왔는데",
    "{city} 갔다가 {situation}에서",
    "오늘 {situation} 들렁는데",
    "{city} 여행중에 {situation} 들렁는데",
    "{situation} 갔다가 발견한건데",
]

OPENING_HOOKS_DEBATE = [
    "이거 한국에 왜 없냐 진짜",
    "이거 먹고 나서 한국 {product_category} 못 먹겠어됨",
    "솔직히 {city} 가기 전엔 이런 거 있는지도 몰랐어",
    "치니덜 이거 아직도 모르면 손해야",
    "{city} 사람들은 이거 당연하게 먹는데 우린 왜..",
]

OPENING_HOOKS_TENSION = [
    "스치나들...오늘 얄직하게 말할게",
    "진짜 {city} {situation} 다들 알아?",
    "{city} 알고리즘이 자꾸 보여주는거야..",
    "새벽에 눈딯는데 {city} {situation}이 뜨는거야..",
    "이거 말하면 다들 왜 이제야 알았냐 할 것 같아서..",
    "나 {city} 갔다가 진짜 충격받은 거 있어",
]

OPENING_HOOKS_COMPARISON = [
    "{city} {situation} 세 군데 다 가봤어",
    "이거 vs 한국 거 비교해봤는데",
    "일본 {situation} 베스트 골라봤어",
    "이거랑 비슷한 거 한국에서도 팔던데 차이가..",
]

OPENING_HOOKS_HOT_TAKE = [
    "진짜로 이거 없으면 {city} 여행 의미없음",
    "스친이들 이거 안 사면 후회함 진짜",
    "나 이제 이거 없으면 못 살 것 같아",
    "이거 한국 들어오면 난 매달 주문할 것 같아",
]


def _pick_opening_hook(packet) -> str:
    categories = [
        OPENING_HOOKS_DISCOVERY,
        OPENING_HOOKS_DEBATE,
        OPENING_HOOKS_TENSION,
        OPENING_HOOKS_COMPARISON,
        OPENING_HOOKS_HOT_TAKE,
    ]
    hooks = random.choice(categories)
    tmpl = random.choice(hooks)
    product_category = packet.product_name.split()[0] if packet.product_name else "과자"
    return tmpl.format(
        city=packet.city,
        situation=packet.situation,
        emotion=packet.emotion,
        product_category=product_category,
    )


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
        "8. 실제 사람이 핸드폰으로 빠르게 타이핑한 것처럼\n"
        "9. '오늘 [도시] 갔는데 [상품] 발견' 패턴 반복 금지 — 오프닝 힌트가 그 구조가 아니면 따르지 말 것"
    )

    _POST_EXAMPLES = (
        "[실제 포스트 예시 1 — 발견체]\n"
        "오늘 삿포로 놀러왔는데 니시마쁜야 있어\n"
        "여기가 유아옷 업청 싸게 팔어\n"
        "진짜 업청싸!\n"
        "스친이들 여기 아는 사람? う\n\n"
        "[실제 포스트 예시 2 — 정보공유체]\n"
        "★★ 파운드 케이크 좋아하는 치니 있었뉅?\n"
        "이거 초코맛은 그냥 브라우니라고 보면 되는\n"
        "넘 고급진 초코 맛\n"
        "한 번에 여러개 주문해서 냉동보관하기 좋앙!\n"
        "치니덜 이런거 먹어뵐뉅?\n\n"
        "[실제 포스트 예시 3 — 얄직고백체]\n"
        "스치나들...얄직히 말할게\n"
        "나 요즘 진짜 힘들었거든\n"
        "근데 이거 써보기 시작하면서 달라\n"
        "나만 이런 거 몰랐던 거야..?\n\n"
        "[실제 포스트 예시 4 — 논쟁훅]\n"
        "이거 한국에 왜 없냐 진짜\n"
        "일본 편의점에서 파는 건데\n"
        "이거 먹고 나서 한국 거 못 먹겠어됨\n"
        "나만 이런 거 아니지?\n\n"
        "[실제 포스트 예시 5 — 슬로우 리빌]\n"
        "나 삿포로 갔다가 진짜 충격받은 게 있어\n"
        "처음엔 그냥 지나치려 했거든\n"
        "근데 옆에 현지인이 바구니에 담길래 나도 집었어\n"
        "진짜 이게 이렇게 맛있을 줄이야\n"
        "스친이들 이거 알고 있었어?"
    )

    def __init__(self, provider=None):
        self._ai = provider or get_ai_provider()

    def _pick_variants(self, packet):
        structure = random.choice(list(STRUCTURE_VARIANTS.values()))
        tone = random.choice(list(TONE_VARIANTS.values()))
        hook = _pick_opening_hook(packet)
        follower_term = random.choice(FOLLOWER_TERMS)
        return structure, tone, hook, follower_term

    def _build_system_prompt(self, structure, tone, follower_term):
        typo_samples = random.sample(PHONE_TYPO_EXAMPLES, 2)
        typo_hint = ", ".join(f'"{a}" 대신 "{b}"처럼' for a, b in typo_samples)
        return (
            "너는 일본 직구 상품을 직접 써본 한국인 Threads 사용자야.\n"
            "팔로워들한테 자연스러운 일상 공유처럼 글을 써.\n"
            "매번 똑같은 패턴('오늘 어디 갔는데 발견')은 쓰지 마 — 오프닝 힌트를 적극 따를 것.\n\n"
            + self._POST_EXAMPLES + "\n\n---\n"
            + f'[이번 글 팔로워 호칭] → "{follower_term}"\n'
            + f'[이번 글 구조] {structure["label"]}: {structure["hint"]}\n'
            + f'[이번 글 톤] {tone["label"]}: {tone["instruction"]}\n'
            + f"[핸드폰 오타 규칙] 이번 글에 {typo_hint} 스타일 오타 포함.\n"
            + "[글 길이] 최대 8줄. 권장 6~8줄.\n\n"
            + self._SAFETY_RULES + "\n\n"
            + "출력 형식 (JSON만):\n"
            + '{"content": "Threads 본문 (줄바꿈은 \\n으로)", '
            + '"first_comment": "첫 댓글", '
            + '"faq": [{"question": "질문1", "answer": "답변1"}, {"question": "질문2", "answer": "답변2"}]}'
        )

    def _build_user_prompt(self, packet, hook, follower_term):
        features = ", ".join(packet.product_features[:3]) if packet.product_features else "효과 좋음"
        return (
            "[스토리 컨텍스트]\n"
            f"여행지: {packet.city}\n상황: {packet.situation}\n"
            f"감성: {packet.emotion}\n스토리 테마: {packet.story_theme}\n"
            f'오프닝 방향 (이 분위기로 시작할 것): "{hook}"\n'
            f'팔로워 호칭: "{follower_term}"\n\n'
            "[상품 정보]\n"
            f"상품명: {packet.product_name}\n"
            f"주요 특징: {features}\n스토어: {packet.store_name}\n\n"
            "중요: product_url 절대 본문에 포함 금지.\n"
            "마지막 질문은 단순히 '아는 사람 있어?' 말고 — 의견 유도나 경험 공유 유도로 구체적으로."
        )

    def _parse_output(self, raw):
        raw = raw.strip()
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
            structure, tone, hook, follower_term = self._pick_variants(packet)
            system = self._build_system_prompt(structure, tone, follower_term)
            user = self._build_user_prompt(packet, hook, follower_term)
            logger.info(f"[GEN attempt={attempt}] structure={structure['label']} | tone={tone['label']} | hook={hook[:30]}")
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
