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
import time
from typing import List, Dict, Optional, Callable, Optional

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


def _exclude_block(config: Dict) -> str:
    """Prompt block listing already-generated question stubs to prevent duplicates."""
    texts = config.get("excluded_texts", [])
    if not texts:
        return ""
    # Send ALL exclusions — no cap. Truncate each stub to EXCLUDE_STUB_LEN chars.
    lines = "\n".join(f"  [{idx+1}] {t[:_EXCLUDE_STUB_LEN]}" for idx, t in enumerate(texts))
    return (
        f"\n\n{'='*60}\n"
        f"ABSOLUTE DUPLICATE BAN — THIS IS NON-NEGOTIABLE:\n"
        f"{'='*60}\n"
        f"The following {len(texts)} question(s) have ALREADY BEEN GENERATED in previous batches "
        f"or already exist in the database.\n"
        f"You are STRICTLY FORBIDDEN from:\n"
        f"  • Reproducing any of these questions verbatim\n"
        f"  • Rephrasing or paraphrasing any of these questions\n"
        f"  • Generating questions that test the same specific fact in the same way\n"
        f"Every single question you output MUST be completely unique and MUST NOT overlap "
        f"with any question listed below. Violating this rule makes the entire output useless.\n\n"
        f"BANNED QUESTIONS:\n{lines}\n"
        f"{'='*60}\n\n"
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
_MIN_TEXT_CHARS_PER_PAGE = 150  # Below this avg, PDF is treated as image-based
_MAX_INLINE_PDF_BYTES = 15 * 1024 * 1024  # 15 MB — above this use File API
_BATCH_SIZE = 20  # max questions per single Gemini call to prevent JSON truncation
_DEDUP_KEY_LEN = 80   # chars used for dedup fingerprint (first N chars of question_text)
_EXCLUDE_STUB_LEN = 160  # chars sent to AI in the banned-questions block


def _dedup_key(text: str) -> str:
    """Normalised fingerprint for dedup: lowercase, collapse whitespace, strip punctuation."""
    t = text.lower().strip()
    # Collapse all whitespace variants (newlines, tabs, multiple spaces)
    t = re.sub(r'\s+', ' ', t)
    # Strip LaTeX wrappers so "$\large x$" and "x" don't appear distinct
    t = re.sub(r'\$[^$]*\$', '', t).strip()
    # Remove common punctuation noise
    t = re.sub(r'[^\w\s]', '', t)
    return t[:_DEDUP_KEY_LEN]


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
        """
        Returns True if PDF has insufficient extractable text.
        Checks text density (avg chars/page), not just presence.
        Handles scanned PDFs, hybrid PDFs, and PDFs with garbage-text layers.
        """
        try:
            reader = PdfReader(pdf_path)
            if not reader.pages:
                return True
            total_chars = sum(
                len((page.extract_text() or "").strip())
                for page in reader.pages
            )
            avg_chars_per_page = total_chars / len(reader.pages)
            needs_vision = avg_chars_per_page < _MIN_TEXT_CHARS_PER_PAGE
            if needs_vision:
                print(f"Image-PDF detected — avg {avg_chars_per_page:.0f} chars/page "
                      f"(threshold: {_MIN_TEXT_CHARS_PER_PAGE}). Switching to Vision mode.")
            return needs_vision
        except Exception:
            return True  # Unreadable by pypdf → try vision

    @staticmethod
    def _retry_call(fn: Callable, max_retries: int = 3, base_delay: float = 5.0):
        """Retry a Gemini API call on transient errors (503/UNAVAILABLE) with exponential backoff."""
        for attempt in range(max_retries + 1):
            try:
                return fn()
            except Exception as e:
                err = str(e)
                is_transient = any(x in err for x in ('503', 'UNAVAILABLE', 'Resource has been exhausted', 'overloaded'))
                if is_transient and attempt < max_retries:
                    wait = base_delay * (2 ** attempt)  # 5s → 10s → 20s
                    print(f"Gemini transient error (attempt {attempt + 1}/{max_retries}) — retrying in {wait:.0f}s...")
                    time.sleep(wait)
                else:
                    raise

    def _upload_pdf(self, pdf_path: str) -> str:
        """Upload PDF via File API once — returns URI for reuse across batches."""
        file_size = os.path.getsize(pdf_path)
        print(f"Uploading PDF ({file_size / (1024*1024):.1f} MB) via File API.")
        uploaded = self.client.files.upload(
            file=pdf_path,
            config={"mime_type": "application/pdf"},
        )
        return uploaded.uri

    def _generate_vision_with_uri(self, file_uri: str, prompt: str) -> str:
        """Call Gemini with a pre-uploaded PDF URI. Reusable across batches. Retries on 503."""
        try:
            return self._retry_call(lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part(file_data=types.FileData(file_uri=file_uri, mime_type="application/pdf")),
                    types.Part(text=prompt),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=65536,
                ),
            ).text)
        except Exception as e:
            raise Exception(f"Gemini Vision PDF error: {str(e)}")

    def _generate_vision_inline(self, pdf_path: str, prompt: str) -> str:
        """Call Gemini with inline base64 PDF (small PDFs <= 15 MB). Retries on 503."""
        import base64
        try:
            with open(pdf_path, "rb") as f:
                pdf_b64 = base64.standard_b64encode(f.read()).decode("utf-8")
            return self._retry_call(lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=[
                    types.Part(inline_data=types.Blob(mime_type="application/pdf", data=pdf_b64)),
                    types.Part(text=prompt),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.3,
                    max_output_tokens=65536,
                ),
            ).text)
        except Exception as e:
            raise Exception(f"Gemini Vision PDF error: {str(e)}")

    # ------------------------------------------------------------------
    # Gemini API call
    # ------------------------------------------------------------------

    def generate_text(self, prompt: str) -> str:
        """Single Gemini call with JSON mode, low temperature, and transient-error retry."""
        try:
            return self._retry_call(lambda: self.client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type='application/json',
                    temperature=0.3,
                    max_output_tokens=65536,
                ),
            ).text)
        except Exception as e:
            raise Exception(f"Gemini API error: {str(e)}")

    # ------------------------------------------------------------------
    # Generation modes
    # ------------------------------------------------------------------

    def extract_from_pdf(self, pdf_path: str, config: Dict, progress_callback=None) -> List[Dict]:
        """Card A: Extract existing questions from PDF. Progress callback, per-batch error recovery."""
        def _cb(event: dict):
            if progress_callback:
                progress_callback(event)

        batches = self._split_batches(config)
        is_vision = self.is_image_pdf(pdf_path)

        file_uri = None
        context = None
        if is_vision:
            _cb({"type": "vision_detected", "message": "Image-based PDF detected — switching to Vision mode."})
            file_size = os.path.getsize(pdf_path)
            if file_size > _MAX_INLINE_PDF_BYTES:
                _cb({"type": "uploading", "message": f"Uploading PDF ({file_size / (1024*1024):.1f} MB) to Gemini File API..."})
                file_uri = self._upload_pdf(pdf_path)
                _cb({"type": "uploaded", "message": "PDF uploaded. Starting batch extraction..."})
        else:
            context = self.extract_pdf_text(pdf_path)

        _cb({"type": "batches_ready", "total_batches": len(batches),
             "message": f"Starting {len(batches)} batch(es)..."})

        # Track how many questions have been extracted so far for sequential offset
        total_extracted_so_far = len(config.get("excluded_texts", []))

        # Build a dedup set from EXISTING excluded_texts so AI-generated duplicates of DB
        # questions get caught in post-generation dedup even if AI ignores the ban block.
        existing_excluded_keys: set = {
            _dedup_key(t) for t in config.get("excluded_texts", []) if t
        }

        all_questions = []
        # seen_keys tracks fingerprints of everything generated THIS session + pre-existing
        seen_keys: set = set(existing_excluded_keys)
        batch_errors = []
        for i, batch_config in enumerate(batches):
            n = i + 1
            # Rolling dedup: add THIS session's already-generated stubs into each batch's exclude list
            session_stubs = [q["question_text"][:_EXCLUDE_STUB_LEN] for q in all_questions]
            existing_stubs = batch_config.get("excluded_texts", [])
            batch_config = dict(batch_config)
            batch_config["excluded_texts"] = existing_stubs + session_stubs

            # Sequential offset so AI picks up where it left off in the PDF
            questions_done = total_extracted_so_far + len(all_questions)

            _cb({"type": "batch_start", "batch": n, "total_batches": len(batches),
                 "questions_so_far": len(all_questions),
                 "message": f"Batch {n}/{len(batches)} — MCQ={batch_config['mcq_count']}, "
                            f"MSQ={batch_config['msq_count']}, NUM={batch_config['numeric_count']}"})
            print(f"Extracting batch {n}/{len(batches)} "
                  f"(MCQ={batch_config['mcq_count']}, MSQ={batch_config['msq_count']}, "
                  f"NUM={batch_config['numeric_count']}) ...")
            try:
                sequential_instruction = (
                    f"\nSEQUENTIAL EXTRACTION ORDER — MANDATORY:\n"
                    f"The PDF contains questions numbered sequentially. "
                    f"{questions_done} question(s) have already been extracted (see BANNED list above).\n"
                    f"You MUST continue from where extraction left off — extract the NEXT "
                    f"{batch_config['mcq_count'] + batch_config['msq_count'] + batch_config['numeric_count']} "
                    f"questions in PDF order (question #{questions_done + 1} onwards).\n"
                    f"Do NOT skip ahead, do NOT go back to earlier questions, do NOT pick randomly.\n"
                ) if questions_done > 0 else (
                    f"\nSEQUENTIAL EXTRACTION ORDER — MANDATORY:\n"
                    f"Extract questions from the PDF in the EXACT ORDER they appear — start from question #1.\n"
                    f"Do NOT pick questions randomly. Follow the PDF sequence strictly.\n"
                )
                prompt = (
                    "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                    "Extract questions EXACTLY as written in the PDF — same question text, same options, same answer.\n"
                    "DO NOT rephrase, DO NOT reorder, DO NOT generate new questions.\n\n"
                    f"{_config_block(batch_config)}\n\n"
                    f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(batch_config['exam_id'])}\n\n"
                    f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n{_exclude_block(batch_config)}"
                    f"{sequential_instruction}\n{_count_line(batch_config)}"
                )
                if is_vision:
                    raw = (self._generate_vision_with_uri(file_uri, prompt)
                           if file_uri else self._generate_vision_inline(pdf_path, prompt))
                else:
                    full_prompt = (
                        "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                        "Extract questions EXACTLY as written — same text, same options, same answer, same order.\n"
                        "DO NOT rephrase, DO NOT reorder, DO NOT generate new questions.\n\n"
                        f"PDF CONTENT:\n{context}\n\n"
                        f"{_config_block(batch_config)}\n\n"
                        f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(batch_config['exam_id'])}\n\n"
                        f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n{_exclude_block(batch_config)}"
                        f"{sequential_instruction}\n{_count_line(batch_config)}"
                    )
                    raw = self.generate_text(full_prompt)
                batch_result = self._parse_and_validate(raw, batch_config)
                # Post-generation dedup: use normalised fingerprint against ALL seen keys
                # (includes DB-existing questions from excluded_texts + this session's output)
                deduped = []
                for q in batch_result:
                    key = _dedup_key(q["question_text"])
                    if key not in seen_keys:
                        deduped.append(q)
                        seen_keys.add(key)
                    else:
                        print(f"[DEDUP] Dropped duplicate: {q['question_text'][:60]}...")
                all_questions.extend(deduped)
                _cb({"type": "batch_done", "batch": n, "total_batches": len(batches),
                     "batch_count": len(deduped), "questions_so_far": len(all_questions),
                     "message": f"Batch {n} done — {len(deduped)} questions extracted ({len(batch_result)-len(deduped)} duplicates dropped)."})
            except Exception as e:
                msg = f"Batch {n} failed: {str(e)}"
                print(msg)
                batch_errors.append(msg)
                _cb({"type": "batch_error", "batch": n, "total_batches": len(batches),
                     "questions_so_far": len(all_questions), "message": msg})

        if not all_questions:
            raise Exception(f"All {len(batches)} batch(es) failed. "
                            f"Last error: {batch_errors[-1] if batch_errors else 'unknown'}")
        return all_questions

    def mine_concepts(self, pdf_path: str, config: Dict, progress_callback=None) -> List[Dict]:
        """Card B: Generate new questions from theory/concepts. Progress callback, per-batch error recovery."""
        def _cb(event: dict):
            if progress_callback:
                progress_callback(event)

        batches = self._split_batches(config)
        is_vision = self.is_image_pdf(pdf_path)

        file_uri = None
        context = None
        if is_vision:
            _cb({"type": "vision_detected", "message": "Image-based PDF detected — switching to Vision mode."})
            file_size = os.path.getsize(pdf_path)
            if file_size > _MAX_INLINE_PDF_BYTES:
                _cb({"type": "uploading", "message": f"Uploading PDF ({file_size / (1024*1024):.1f} MB) to Gemini File API..."})
                file_uri = self._upload_pdf(pdf_path)
                _cb({"type": "uploaded", "message": "PDF uploaded. Starting concept mining..."})
        else:
            context = self.extract_pdf_text(pdf_path)

        _cb({"type": "batches_ready", "total_batches": len(batches),
             "message": f"Starting {len(batches)} batch(es)..."})

        existing_excluded_keys: set = {
            _dedup_key(t) for t in config.get("excluded_texts", []) if t
        }
        all_questions = []
        seen_keys: set = set(existing_excluded_keys)
        batch_errors = []
        for i, batch_config in enumerate(batches):
            n = i + 1
            # Rolling dedup: feed this session's already-generated stubs as banned list
            session_stubs = [q["question_text"][:_EXCLUDE_STUB_LEN] for q in all_questions]
            existing_stubs = batch_config.get("excluded_texts", [])
            batch_config = dict(batch_config)
            batch_config["excluded_texts"] = existing_stubs + session_stubs

            _cb({"type": "batch_start", "batch": n, "total_batches": len(batches),
                 "questions_so_far": len(all_questions),
                 "message": f"Batch {n}/{len(batches)} — MCQ={batch_config['mcq_count']}, "
                            f"MSQ={batch_config['msq_count']}, NUM={batch_config['numeric_count']}"})
            print(f"Mining batch {n}/{len(batches)} "
                  f"(MCQ={batch_config['mcq_count']}, MSQ={batch_config['msq_count']}, "
                  f"NUM={batch_config['numeric_count']}) ...")
            try:
                prompt = (
                    "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                    "Generate NEW, HIGH-QUALITY questions based ONLY on the topic/content in this PDF.\n"
                    "DO NOT generate questions about unrelated topics.\n\n"
                    f"{_config_block(batch_config)}\n\n"
                    f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(batch_config['exam_id'])}\n\n"
                    f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
                    f"Match the '{batch_config['difficulty']}' difficulty level.\n"
                    f"{_exclude_block(batch_config)}{_count_line(batch_config)}"
                )
                if is_vision:
                    raw = (self._generate_vision_with_uri(file_uri, prompt)
                           if file_uri else self._generate_vision_inline(pdf_path, prompt))
                else:
                    full_prompt = (
                        "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                        "Generate NEW, HIGH-QUALITY questions from the theory content below.\n"
                        "DO NOT generate questions about unrelated topics.\n\n"
                        f"THEORY CONTENT:\n{context}\n\n"
                        f"{_config_block(batch_config)}\n\n"
                        f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(batch_config['exam_id'])}\n\n"
                        f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
                        f"Match the '{batch_config['difficulty']}' difficulty level.\n"
                        f"{_exclude_block(batch_config)}{_count_line(batch_config)}"
                    )
                    raw = self.generate_text(full_prompt)
                batch_result = self._parse_and_validate(raw, batch_config)
                # Post-generation dedup using normalised fingerprint vs ALL seen keys
                deduped = []
                for q in batch_result:
                    key = _dedup_key(q["question_text"])
                    if key not in seen_keys:
                        deduped.append(q)
                        seen_keys.add(key)
                    else:
                        print(f"[DEDUP] Dropped duplicate: {q['question_text'][:60]}...")
                all_questions.extend(deduped)
                _cb({"type": "batch_done", "batch": n, "total_batches": len(batches),
                     "questions_so_far": len(all_questions),
                     "message": f"Batch {n} done — {len(deduped)} questions mined ({len(batch_result)-len(deduped)} duplicates dropped)."})
            except Exception as e:
                msg = f"Batch {n} failed: {str(e)}"
                print(msg)
                batch_errors.append(msg)
                _cb({"type": "batch_error", "batch": n, "total_batches": len(batches),
                     "questions_so_far": len(all_questions), "message": msg})

        if not all_questions:
            raise Exception(f"All {len(batches)} batch(es) failed. "
                            f"Last error: {batch_errors[-1] if batch_errors else 'unknown'}")
        return all_questions

    def generate_from_topic(self, topic: str, config: Dict, progress_callback=None) -> List[Dict]:
        """Card C: Pure generation from topic name — no PDF needed. Batches large counts."""
        def _cb(event: dict):
            if progress_callback:
                progress_callback(event)

        batches = self._split_batches(config)
        _cb({"type": "batches_ready", "total_batches": len(batches),
             "message": f"Generating {len(batches)} batch(es) on topic: {topic}"})

        all_questions = []
        batch_errors = []
        for i, batch_config in enumerate(batches):
            n = i + 1
            _cb({"type": "batch_start", "batch": n, "total_batches": len(batches),
                 "questions_so_far": len(all_questions),
                 "message": f"Batch {n}/{len(batches)} — MCQ={batch_config['mcq_count']}, "
                            f"MSQ={batch_config['msq_count']}, NUM={batch_config['numeric_count']}"})
            try:
                prompt = (
                    "You are a Professional Question Paper Designer for JEE/NEET level exams.\n"
                    f"Generate HIGH-QUALITY questions on the topic: {topic}\n\n"
                    f"{_config_block(batch_config)}\n\n"
                    f"Return ONLY a valid JSON array. Example schema:\n{_schema_example(batch_config['exam_id'])}\n\n"
                    f"{_LATEX_RULES}\n{_OUTPUT_RULES}\n"
                    f"Match the '{batch_config['difficulty']}' difficulty level.\n"
                    f"{_exclude_block(batch_config)}{_count_line(batch_config)}"
                )
                batch_result = self._parse_and_validate(self.generate_text(prompt), batch_config)
                all_questions.extend(batch_result)
                _cb({"type": "batch_done", "batch": n, "total_batches": len(batches),
                     "batch_count": len(batch_result), "questions_so_far": len(all_questions),
                     "message": f"Batch {n} done — {len(batch_result)} questions generated."})
            except Exception as e:
                msg = f"Batch {n} failed: {str(e)}"
                print(msg)
                batch_errors.append(msg)
                _cb({"type": "batch_error", "batch": n, "total_batches": len(batches),
                     "questions_so_far": len(all_questions), "message": msg})

        if not all_questions:
            raise Exception(f"All {len(batches)} batch(es) failed. "
                            f"Last error: {batch_errors[-1] if batch_errors else 'unknown'}")
        return all_questions

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

    @staticmethod
    def _split_batches(config: Dict) -> List[Dict]:
        """
        Split large question counts into batches of <= _BATCH_SIZE.
        Fills MCQ first, then MSQ, then NUMERIC within each batch slot.
        """
        remaining = {
            'mcq_count':     config.get('mcq_count', 0),
            'msq_count':     config.get('msq_count', 0),
            'numeric_count': config.get('numeric_count', 0),
        }
        total = sum(remaining.values())
        if total <= _BATCH_SIZE:
            return [config]

        batches = []
        while any(v > 0 for v in remaining.values()):
            batch = dict(config)
            slots = _BATCH_SIZE
            for key in ('mcq_count', 'msq_count', 'numeric_count'):
                take = min(remaining[key], slots)
                batch[key] = take
                remaining[key] -= take
                slots -= take
            batches.append(batch)
        return batches

    @staticmethod
    def _salvage_partial_json(text: str) -> list:
        """
        Gemini sometimes truncates JSON mid-stream when output is long.
        Walk the string tracking brace depth to recover all fully-closed objects.
        """
        if not text.strip().startswith('['):
            return []
        try:
            last_good = 0
            depth = 0
            in_string = False
            skip_next = False
            for i, ch in enumerate(text):
                if skip_next:
                    skip_next = False
                    continue
                if ch == '\\' and in_string:
                    skip_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        last_good = i + 1  # end of a complete top-level object
            if last_good == 0:
                return []
            salvaged = '[' + text[1:last_good] + ']'
            salvaged = re.sub(r',\s*]', ']', salvaged)
            return json.loads(salvaged)
        except Exception:
            return []

    def _parse_and_validate(self, llm_output: str, config: Dict) -> List[Dict]:
        """Parse LLM JSON output and validate with Pydantic. Auto-salvages truncated output."""
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
            # Attempt to salvage complete objects before giving up
            salvaged_raw = self._salvage_partial_json(cleaned)
            if salvaged_raw:
                validated = []
                for q in salvaged_raw:
                    try:
                        validated.append(QuestionModel(**q).dict())
                    except Exception:
                        pass
                if validated:
                    print(f"JSON truncated — salvaged {len(validated)} complete questions from partial output.")
                    return validated
            # Categorise the error for user-friendly display
            err_str = str(e)
            is_cut = any(x in err_str for x in ("Unterminated", "Expecting property"))
            friendly = (
                "TRUNCATED: AI response was cut off mid-output. "
                "Reduce question count — try 20 or fewer per session."
            ) if is_cut else (
                "MALFORMED: AI returned invalid JSON. Try again."
            )
            raise Exception(friendly)
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
    progress_callback=None,
) -> List[Dict]:
    """
    Generate questions via AI.
    mode: 'extract' | 'mine' | 'pure'
    progress_callback: optional callable(event: dict) for live progress updates.
    Raises a clean Exception on failure — never crashes the caller.
    """
    generator = AIQuestionGenerator()

    if mode == 'extract':
        if not pdf_path:
            raise ValueError("PDF path required for extraction mode")
        return generator.extract_from_pdf(pdf_path, config, progress_callback)
    elif mode == 'mine':
        if not pdf_path:
            raise ValueError("PDF path required for concept mining mode")
        return generator.mine_concepts(pdf_path, config, progress_callback)
    elif mode == 'pure':
        if not topic:
            raise ValueError("Topic required for pure generation mode")
        return generator.generate_from_topic(topic, config, progress_callback)
    else:
        raise ValueError(f"Invalid mode: {mode}")