"""
AI Question Generator Engine
Uses Google GenAI SDK directly + pypdf for PDF text extraction.

ROOT CAUSE FIX:
  The old implementation used GoogleGenerativeAIEmbeddings (langchain_google_genai)
  alongside google.genai Client. Both try to initialize SSL/gRPC transports,
  causing "maximum recursion depth exceeded".

SOLUTION:
  - Removed FAISS vector store and LangChain embeddings entirely.
  - PDF text is extracted with pypdf (already in requirements.txt).
  - Context is passed straight to Gemini — no embedding pipeline.
  - Eliminates the SSL recursion and speeds up generation significantly.
"""

import os
import json
import re
from typing import List, Dict, Optional

from pydantic import BaseModel, Field, validator
from pypdf import PdfReader
from google import genai
from google.genai import types


# ========================
# LATEX SANITIZER
# ========================

def _is_pure_text_block(content: str) -> bool:
    """
    Returns True if a LaTeX block contains ONLY \text{...} with no real math.
    These should be unwrapped to plain text instead of kept as LaTeX.
    e.g.  \large \text{What is mortar?}  →  plain text, not LaTeX
    e.g.  \large \frac{a}{b}             →  real math, keep as LaTeX
    """
    stripped = content.strip()
    # Remove size commands temporarily
    for s in ['\\large', '\\Large', '\\LARGE', '\\small', '\\normalsize', '\\huge', '\\Huge']:
        stripped = stripped.replace(s, '').strip()
    # If what remains is purely \text{...} (with nothing else), it's plain text
    text_only = re.fullmatch(r'\\text\s*\{([^}]*)\}', stripped)
    return text_only is not None


def sanitize_latex(text: str) -> str:
    """
    Cleans LaTeX in mixed text:
    - Unwraps $\large \text{plain english}$ back to plain english
    - Keeps genuine math blocks and adds \large to them
    - Standardizes $$ and \[...\] to single $
    """
    if not text:
        return text

    # 1. Standardize wrappers
    text = text.replace('$$', '$').replace(r'\[', '$').replace(r'\]', '$')

    def normalize_block(match):
        content = match.group(1).strip()

        # Remove size commands to inspect real content
        cleaned = content
        for s in ['small', 'normalsize', 'large', 'Large', 'LARGE', 'huge', 'Huge']:
            cleaned = re.sub(rf'\\{s}\b', '', cleaned).strip()

        # If the entire block is just \text{some plain sentence}, unwrap it
        text_only = re.fullmatch(r'\\text\s*\{([^}]*)\}', cleaned)
        if text_only:
            return text_only.group(1).strip()

        # Otherwise it's real math — keep as LaTeX with \large
        return f'$\\large {cleaned}$'

    text = re.sub(r'\$([^$]+)\$', normalize_block, text)
    return text.strip()


# ========================
# PYDANTIC MODELS
# ========================

class QuestionModel(BaseModel):
    """Strict validation model for generated questions"""
    exam_id: int
    question_text: str
    option_a: str = ""
    option_b: str = ""
    option_c: str = ""
    option_d: str = ""
    correct_answer: str
    question_type: str = Field(default="MCQ")
    positive_marks: float = Field(default=4.0)
    negative_marks: float = Field(default=1.0)
    tolerance: float = Field(default=0.0)

    @validator('question_text', 'option_a', 'option_b', 'option_c', 'option_d', pre=True, always=True)
    def sanitize_fields(cls, v):
        """Validates and cleans fields for mixed text/latex content."""
        if v and str(v).strip() not in ('', 'nan', 'None'):
            return sanitize_latex(str(v))
        return ""

    @validator('question_type')
    def validate_question_type(cls, v):
        if v not in ['MCQ', 'MSQ', 'NUMERIC']:
            raise ValueError('question_type must be MCQ, MSQ, or NUMERIC')
        return v

    @validator('option_a', 'option_b', 'option_c', 'option_d')
    def validate_options_numeric(cls, v, values):
        if values.get('question_type') == 'NUMERIC':
            return ""
        return v

    class Config:
        extra = 'forbid'


# ========================
# PROMPT TEMPLATES
# ========================

_LATEX_RULES = r"""
CRITICAL FORMATTING RULES — READ CAREFULLY:

RULE 1 — PLAIN TEXT IS DEFAULT:
  Write ALL question text, option text in PLAIN ENGLISH by default.
  Do NOT wrap normal English words or sentences in LaTeX.

RULE 2 — LaTeX ONLY for math/science symbols:
  Use $...$ ONLY for: variables, equations, fractions, Greek letters,
  units in formulas, chemical formulas, or mathematical expressions.
  CORRECT:   "The moment of inertia is $\large I = \frac{MR^2}{2}$."
  CORRECT:   "A block of mass $\large m$ slides with velocity $\large v$."
  CORRECT:   "What is the primary function of mortar in construction?"  <- NO LaTeX needed here
  WRONG:     "$\large \text{What is the primary function of mortar?}$"  <- NEVER do this

RULE 3 — \text{} is FORBIDDEN:
  NEVER use \text{} inside dollar signs. Plain English goes outside $...$.

RULE 4 — Every LaTeX block needs \large:
  CORRECT: $\large \frac{a}{b}$     WRONG: $\frac{a}{b}$

RULE 5 — Zero-math questions use ZERO LaTeX.
  If a question has no math/symbols, write it in 100% plain text.
"""

_OUTPUT_RULES = r"""
- NUMERIC: options A,B,C,D MUST be empty strings ""
- MSQ: correct_answer can be "A,C" or "A,B,D"
- MCQ: correct_answer must be single letter
"""


def _config_block(config: Dict) -> str:
    return (
        f"CONFIGURATION:\n"
        f"- Exam ID: {config['exam_id']}\n"
        f"- Difficulty: {config['difficulty']}\n"
        f"- Counts: MCQ={config.get('mcq_count', 0)}, "
        f"MSQ={config.get('msq_count', 0)}, "
        f"NUMERIC={config.get('numeric_count', 0)}\n"
        f"- MCQ marks: +{config.get('mcq_plus', 4)}/-{config.get('mcq_minus', 1)}\n"
        f"- MSQ marks: +{config.get('msq_plus', 4)}/-{config.get('msq_minus', 2)}\n"
        f"- NUMERIC marks: +{config.get('numeric_plus', 3)}, "
        f"tolerance={config.get('numeric_tolerance', 0.01)}\n"
        f"- Custom Instructions: {config.get('custom_instructions', 'None')}"
    )


def _count_line(config: Dict) -> str:
    return (
        f"Generate exactly {config.get('mcq_count', 0)} MCQ, "
        f"{config.get('msq_count', 0)} MSQ, and "
        f"{config.get('numeric_count', 0)} NUMERIC questions."
    )


def _schema_example(exam_id: int) -> str:
    """
    Two examples are shown:
    1. A math-heavy question (correct LaTeX usage)
    2. A theory/text question (zero LaTeX — plain text only)
    This teaches the AI when to use LaTeX and when NOT to.
    """
    return (
        '[\n'
        '  {\n'
        f'    "exam_id": {exam_id},\n'
        '    "question_text": "A block of mass $\\large m$ slides with velocity $\\large v$ on a frictionless surface. Its kinetic energy is:",\n'
        '    "option_a": "$\\large \\frac{1}{2}mv^2$",\n'
        '    "option_b": "$\\large mv^2$",\n'
        '    "option_c": "$\\large 2mv^2$",\n'
        '    "option_d": "$\\large \\frac{mv^2}{4}$",\n'
        '    "correct_answer": "A",\n'
        '    "question_type": "MCQ",\n'
        '    "positive_marks": 4.0,\n'
        '    "negative_marks": 1.0,\n'
        '    "tolerance": 0.0\n'
        '  },\n'
        '  {\n'
        f'    "exam_id": {exam_id},\n'
        '    "question_text": "Which of the following is NOT a primary ingredient of mortar?",\n'
        '    "option_a": "Binder",\n'
        '    "option_b": "Fine aggregate",\n'
        '    "option_c": "Coarse aggregate",\n'
        '    "option_d": "Water",\n'
        '    "correct_answer": "C",\n'
        '    "question_type": "MCQ",\n'
        '    "positive_marks": 4.0,\n'
        '    "negative_marks": 1.0,\n'
        '    "tolerance": 0.0\n'
        '  }\n'
        ']'
    )


# ========================
# CORE ENGINE
# ========================

_MAX_CONTEXT_CHARS = 12000  # ~3k tokens — fast and sufficient


class AIQuestionGenerator:
    """
    Fast, stable question generator.
    - pypdf for direct PDF text extraction (no FAISS, no LangChain embeddings).
    - Single google.genai Client — no SSL recursion possible.
    - Clean error handling — never crashes the caller.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.environ.get('GEMINI_API_KEY')
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY not found in environment")
        model_raw = os.environ.get('GEMINI_MODEL_NAME', 'gemini-1.5-flash')
        self.model_name = model_raw.replace('models/', '')
        self.client = genai.Client(api_key=self.api_key)
        print(f"AI Question Generator ready — model: {self.model_name}")

    # ------------------------------------------------------------------
    # PDF text extraction
    # ------------------------------------------------------------------

    def extract_pdf_text(self, pdf_path: str) -> str:
        """
        Extract text from PDF using pypdf.
        Returns empty string if PDF is image-based (no text layer).
        Caller should check and fall back to vision mode.
        """
        try:
            reader = PdfReader(pdf_path)
            parts = []
            total = 0
            for page in reader.pages:
                text = page.extract_text() or ""
                parts.append(text)
                total += len(text)
                if total >= _MAX_CONTEXT_CHARS:
                    break
            return "\n\n".join(parts)[:_MAX_CONTEXT_CHARS]
        except Exception as e:
            raise Exception(f"PDF extraction failed: {str(e)}")

    def is_image_pdf(self, pdf_path: str) -> bool:
        """Returns True if PDF has no extractable text (scanned/image-based)."""
        try:
            reader = PdfReader(pdf_path)
            for page in reader.pages:
                if (page.extract_text() or "").strip():
                    return False
            return True
        except Exception:
            return True

    def generate_text_with_pdf_vision(self, pdf_path: str, prompt_suffix: str) -> str:
        """
        Send PDF directly to Gemini using File API — works for image-based PDFs.
        Gemini natively reads PDF images, so no OCR step needed.
        """
        import base64
        try:
            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part(
                        inline_data=types.Blob(
                            mime_type="application/pdf",
                            data=pdf_b64,
                        )
                    ),
                    types.Part(text=prompt_suffix),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                ),
            )
            return response.text
        except Exception as e:
            raise Exception(f"Gemini Vision PDF error: {str(e)}")

    # ------------------------------------------------------------------
    # Gemini API call
    # ------------------------------------------------------------------

    def generate_text(self, prompt: str) -> str:
        """Single Gemini call with JSON mode and low temperature for accuracy."""
        try:
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.3,  # Set low to ensure strict instruction following
                ),
            )
            return response.text
        except Exception as e:
            raise Exception(f"Gemini API error: {str(e)}")

    # ------------------------------------------------------------------
    # Generation modes
    # ------------------------------------------------------------------

    def extract_from_pdf(self, pdf_path: str, config: Dict) -> List[Dict]:
        """Card A: Extract existing questions from PDF. Auto-detects image-based PDFs."""
        base_prompt = (
            "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
            "Extract questions ONLY from the content of this PDF. DO NOT generate unrelated questions.\n\n"
            f"{_config_block(config)}\n\n"
            f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(config['exam_id'])}\n\n"
            f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n{_count_line(config)}"
        )
        if self.is_image_pdf(pdf_path):
            print("Detected image-based PDF — using Gemini Vision mode.")
            return self._parse_and_validate(
                self.generate_text_with_pdf_vision(pdf_path, base_prompt), config
            )
        else:
            context = self.extract_pdf_text(pdf_path)
            prompt = (
                "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                "Extract questions ONLY from the provided PDF content. DO NOT generate unrelated questions.\n\n"
                f"PDF CONTENT:\n{context}\n\n"
                f"{_config_block(config)}\n\n"
                f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(config['exam_id'])}\n\n"
                f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n{_count_line(config)}"
            )
            return self._parse_and_validate(self.generate_text(prompt), config)

    def mine_concepts(self, pdf_path: str, config: Dict) -> List[Dict]:
        """Card B: Generate new questions from theory/concepts. Auto-detects image-based PDFs."""
        base_prompt = (
            "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
            "Generate NEW, HIGH-QUALITY questions based ONLY on the topic/content in this PDF.\n"
            "DO NOT generate questions about unrelated topics.\n\n"
            f"{_config_block(config)}\n\n"
            f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(config['exam_id'])}\n\n"
            f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
            f"Match the '{config['difficulty']}' difficulty level.\n"
            f"{_count_line(config)}"
        )
        if self.is_image_pdf(pdf_path):
            print("Detected image-based PDF — using Gemini Vision mode.")
            return self._parse_and_validate(
                self.generate_text_with_pdf_vision(pdf_path, base_prompt), config
            )
        else:
            context = self.extract_pdf_text(pdf_path)
            prompt = (
                "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                "Generate NEW, HIGH-QUALITY questions from the theory content below.\n"
                "DO NOT generate questions about unrelated topics.\n\n"
                f"THEORY CONTENT:\n{context}\n\n"
                f"{_config_block(config)}\n\n"
                f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(config['exam_id'])}\n\n"
                f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
                f"Match the '{config['difficulty']}' difficulty level.\n"
                f"{_count_line(config)}"
            )
            return self._parse_and_validate(self.generate_text(prompt), config)

    def generate_from_topic(self, topic: str, config: Dict) -> List[Dict]:
        """Card C: Pure generation from topic name — no PDF needed."""
        prompt = (
            "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
            f"Generate HIGH-QUALITY questions on the topic: {topic}\n\n"
            f"{_config_block(config)}\n\n"
            f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(config['exam_id'])}\n\n"
            f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
            f"Match the '{config['difficulty']}' difficulty level.\n"
            f"{_count_line(config)}"
        )
        return self._parse_and_validate(self.generate_text(prompt), config)

    # ------------------------------------------------------------------
    # JSON parsing + Pydantic validation
    # ------------------------------------------------------------------

    def _fix_invalid_escapes(self, raw: str) -> str:
        """
        Double-escape bare LaTeX backslashes inside JSON strings so json.loads works.
        Only \\\\  and \\\"  are valid JSON escape sequences.
        All other backslash sequences are LaTeX — double them.
        """
        result = []
        in_string = False
        i = 0
        while i < len(raw):
            ch = raw[i]
            if ch == '"' and (i == 0 or raw[i - 1] != '\\'):
                in_string = not in_string
                result.append(ch)
                i += 1
            elif ch == '\\' and in_string:
                next_ch = raw[i + 1] if i + 1 < len(raw) else ''
                if next_ch in ('\\', '"'):
                    result.append(ch)
                    result.append(next_ch)
                    i += 2
                else:
                    result.append('\\\\')
                    i += 1
            else:
                result.append(ch)
                i += 1
        return ''.join(result)

    def _parse_and_validate(self, llm_output: str, config: Dict) -> List[Dict]:
        """Parse LLM JSON output and validate with Pydantic."""
        try:
            cleaned = llm_output.strip()
            cleaned = re.sub(r'^```json\s*', '', cleaned)
            cleaned = re.sub(r'^```\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned)
            cleaned = cleaned.strip()
            cleaned = self._fix_invalid_escapes(cleaned)

            questions = json.loads(cleaned)
            if not isinstance(questions, list):
                raise ValueError("Output must be a JSON array")

            validated = []
            for q in questions:
                try:
                    validated.append(QuestionModel(**q).dict())
                except Exception as e:
                    print(f"Skipping invalid question: {e}")

            if not validated:
                raise ValueError("No valid questions were generated")
            return validated

        except json.JSONDecodeError as e:
            raise Exception(
                f"Invalid JSON from AI: {str(e)}\nOutput preview: {llm_output[:500]}"
            )
        except Exception as e:
            raise Exception(f"Validation failed: {str(e)}")


# ========================
# PUBLIC ENTRY POINT
# ========================

def generate_questions(
    mode: str,
    config: Dict,
    pdf_path: Optional[str] = None,
    topic: Optional[str] = None,
) -> List[Dict]:
    """
    Generate questions via AI.
    mode: 'extract' | 'mine' | 'pure'
    Raises a clean Exception on failure — never crashes the caller.
    """
    generator = AIQuestionGenerator()

    if mode == 'extract':
        if not pdf_path:
            raise ValueError("PDF path required for extraction mode")
        return generator.extract_from_pdf(pdf_path, config)
    elif mode == 'mine':
        if not pdf_path:
            raise ValueError("PDF path required for concept mining mode")
        return generator.mine_concepts(pdf_path, config)
    elif mode == 'pure':
        if not topic:
            raise ValueError("Topic required for pure generation mode")
        return generator.generate_from_topic(topic, config)
    else:
        raise ValueError(f"Invalid mode: {mode}")