# file path: nlp_engine/analyzer.py
import json
import logging
from pydantic import BaseModel, ValidationError, Field
from openai import AsyncOpenAI, OpenAIError
from config.settings import Settings

# 로거 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Pydantic 모델을 통한 강력한 JSON 검증 구조
class SentimentResult(BaseModel):
    reasoning: str = Field(..., description="Short explanation of the reasoning")
    sentiment_score: float = Field(..., ge=-1.0, le=1.0, description="Score between -1.0 and 1.0")
    confidence: int = Field(..., ge=0, le=100, description="Confidence level from 0 to 100")

class SentimentAnalyzer:
    """
    OpenAI GPT API를 비동기식(asyncio)으로 호출하여 뉴스 텍스트의 감성을 평가하는 클래스입니다.
    향후 고빈도 혹은 병렬 처리를 위해 AsyncOpenAI 클라이언트를 사용합니다.
    """
    def __init__(self, settings: Settings, use_local_llm: bool = False):
        self.settings = settings
        self.use_local_llm = use_local_llm
        
        # 비동기 OpenAI 클라이언트 생성 (로컬 LLM 스위칭)
        if self.use_local_llm:
            self.client = AsyncOpenAI(
                base_url="http://localhost:11434/v1",
                api_key="ollama" # 필요한 포맷을 맞추기 위한 더미 키
            )
        else:
            self.client = AsyncOpenAI(api_key=self.settings.OPENAI_API_KEY)

    async def analyze_sentiment(self, news_text: str) -> tuple[str, float, int]:
        """
        뉴스 텍스트를 인자로 받아 시스템 프롬프트와 함께 비동기적으로 OpenAI API에 요청합니다.
        JSON 응답을 받아 Pydantic으로 한 번 더 검증하고, 추출된 결과(reasoning, sentiment_score, confidence)를 리턴합니다.
        오류 시 시스템 중단을 막기 위해 기본 중립값을 반환합니다.
        """
        system_prompt = f"""You are a highly capable and strict financial sentiment analysis engine for quantitative trading.
Your absolute sole purpose is to analyze the context of the provided financial news piece concerning its potential impact on the subject company's stock price.

First, THINK step-by-step to evaluate the text.
Assign a continuous numerical score between -1.0 (extremely negative / bearish) and 1.0 (extremely positive / bullish). 
Also, assign a confidence score from 0 to 100.
단순 가십이나 영향력이 적은 뉴스는 confidence를 50 이하로 주어라. 실적 발표, 대규모 리콜, 합병 등 펀더멘털에 직결되는 뉴스만 confidence를 80 이상으로 평가해라.

[SCORING GUIDELINES]
- 1.0: Definite massive revenue increase, successful major acquisition, FDA approval, etc.
- 0.5: Positive earnings beat, favorable macro-economic policy, solid product launch.
- 0.0: Neutral, factual reporting with no clear directional impact.
- -0.5: Earnings miss, CEO resignation, minor regulatory scrutiny.
- -1.0: Bankruptcy filing, massive fraud scandal, catastrophic product failure.

CRITICAL INSTRUCTION:
You MUST NOT generate any explanations, greetings, conversational text, or markdown code blocks outside of the JSON object.
Your response MUST be entirely and exclusively a valid JSON object matching the exact format below:
{{
  "reasoning": "짧은 분석 근거",
  "sentiment_score": [float],
  "confidence": [int 0-100]
}}

[INPUT TEXT]
{news_text}
"""
        try:
            # 10초 타임아웃을 설정한 비동기 호출 (응답 형식 JSON 강제)
            model_name = "llama3" if self.use_local_llm else "gpt-4o-mini"
            response = await self.client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "You are a restricted JSON-only output engine."},
                    {"role": "user", "content": system_prompt}
                ],
                response_format={"type": "json_object"},
                timeout=10.0
            )

            raw_content = response.choices[0].message.content
            
            # 파이썬 json 모듈로 1차 파싱
            parsed_json = json.loads(raw_content)
            
            # Pydantic 모델을 통한 2차 강력 검증 (타입 체크 및 -1.0 ~ 1.0 구간 바운딩 체크)
            validated_data = SentimentResult(**parsed_json)
            
            return validated_data.reasoning, validated_data.sentiment_score, validated_data.confidence

        except OpenAIError as e:
            logger.error(f"OpenAI API 네트워크 오류 또는 타임아웃: {e}")
            return "network error", 0.0, 0
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패 (서버가 잘못된 포맷을 리턴함): {e}. Raw content: {raw_content}")
            return "parse error", 0.0, 0
        except ValidationError as e:
            logger.error(f"Pydantic 검증 실패 (값의 범위 오류 등): {e}")
            return "validation error", 0.0, 0
        except Exception as e:
            logger.error(f"알 수 없는 오류 발생: {e}")
            return "unknown error", 0.0, 0
