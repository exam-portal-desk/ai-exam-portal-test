"""
app/utils/latex.py
Converts LaTeX markup to Unicode math text for ReportLab PDF rendering
(PDF has no LaTeX/MathJax engine, so equations are approximated with
Unicode symbols/super-subscripts). Requires a Unicode-capable font
(DejaVu Sans, registered in pdf_service.py) to actually display the
Greek letters, arrows and operators produced here.
"""

import re as _re

_SUPERSCRIPT_MAP = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
    "7": "⁷", "8": "⁸", "9": "⁹", "+": "⁺", "-": "⁻", "=": "⁼",
    "(": "⁽", ")": "⁾", "n": "ⁿ", "i": "ⁱ",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
    "h": "ʰ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "o": "ᵒ", "p": "ᵖ",
    "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ", "v": "ᵛ", "w": "ʷ", "x": "ˣ",
    "y": "ʸ", "z": "ᶻ",
}
_SUBSCRIPT_MAP = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄", "5": "₅", "6": "₆",
    "7": "₇", "8": "₈", "9": "₉", "+": "₊", "-": "₋", "=": "₌",
    "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "o": "ₒ", "x": "ₓ", "h": "ₕ", "k": "ₖ", "l": "ₗ",
    "m": "ₘ", "n": "ₙ", "p": "ₚ", "s": "ₛ", "t": "ₜ",
}


def _superscript(text: str) -> str:
    text = text.replace("^", "").replace("{", "").replace("}", "")
    return "".join(_SUPERSCRIPT_MAP.get(c, c) for c in text)


def _subscript(text: str) -> str:
    text = text.replace("_", "").replace("{", "").replace("}", "")
    return "".join(_SUBSCRIPT_MAP.get(c, c) for c in text)


def _extract_group(s: str, start: int):
    """s[start] must be '{'. Returns (content, index_after_matching_close_brace)."""
    depth, i, n = 0, start, len(s)
    while i < n:
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1:i], i + 1
        i += 1
    return s[start + 1:], n


def _replace_command(s: str, name: str, nargs: int, handler) -> str:
    """Replace \\name{g1}...{gN} with handler(*groups), respecting nested braces."""
    pat = "\\" + name
    out, i = [], 0
    while True:
        idx = s.find(pat, i)
        if idx == -1:
            out.append(s[i:])
            break
        j = idx + len(pat)
        if j < len(s) and s[j].isalpha():
            # longer command name (e.g. \frame when looking for \frac) — not a match
            out.append(s[i:j])
            i = j
            continue
        out.append(s[i:idx])
        pos, groups, ok = j, [], True
        for _ in range(nargs):
            while pos < len(s) and s[pos] in " \t":
                pos += 1
            if pos >= len(s) or s[pos] != "{":
                ok = False
                break
            content, pos = _extract_group(s, pos)
            groups.append(content)
        if ok:
            out.append(handler(*groups))
            i = pos
        else:
            out.append(pat)
            i = j
    return "".join(out)


def _replace_scripts(s: str, marker: str, formatter) -> str:
    """Replace marker{...} or marker<char> (e.g. ^{...}, _x), respecting nested braces."""
    out, i = [], 0
    while True:
        idx = s.find(marker, i)
        if idx == -1:
            out.append(s[i:])
            break
        out.append(s[i:idx])
        pos = idx + 1
        if pos < len(s) and s[pos] == "{":
            content, pos = _extract_group(s, pos)
            out.append(formatter(content))
        elif pos < len(s) and s[pos] not in " \t":
            out.append(formatter(s[pos]))
            pos += 1
        else:
            out.append(marker)
        i = pos
    return "".join(out)


def _format_frac(num: str, den: str) -> str:
    # Short alnum numerator/denominator -> real superscript/subscript fraction glyphs.
    if _re.fullmatch(r"[A-Za-z0-9]{1,3}", num) and _re.fullmatch(r"[A-Za-z0-9]{1,3}", den):
        return _superscript(num) + "⁄" + _subscript(den)
    return f"({num})/({den})"


_CHEM_ARROWS = [("<=>", "⇌"), ("<->", "↔"), ("->", "→"), ("<-", "←")]


def _format_chem(content: str) -> str:
    # mhchem \ce{...}: reaction arrows + element subscripts (digits after a letter/bracket).
    content = content.replace(r"\cdot", "·")  # e.g. CuSO4\cdot5H2O — before digit lookbehind
    for pat, repl in _CHEM_ARROWS:
        content = content.replace(pat, repl)
    content = _re.sub(r"(?<=[A-Za-z\)\]])(\d+)", lambda m: _subscript(m.group(1)), content)
    return content

_REPLACEMENTS = [
    # Arrows
    (r"\\rightarrow(?![a-zA-Z])", "→"), (r"\\leftarrow(?![a-zA-Z])", "←"),
    (r"\\Rightarrow(?![a-zA-Z])", "⇒"), (r"\\Leftarrow(?![a-zA-Z])", "⇐"),
    (r"\\to(?![a-zA-Z])", "→"),
    # Relations
    (r"\\leq(?![a-zA-Z])", "≤"), (r"\\geq(?![a-zA-Z])", "≥"), (r"\\neq(?![a-zA-Z])", "≠"),
    (r"\\approx(?![a-zA-Z])", "≈"), (r"\\equiv(?![a-zA-Z])", "≡"), (r"\\sim(?![a-zA-Z])", "~"),
    (r"\\propto(?![a-zA-Z])", "∝"), (r"\\perp(?![a-zA-Z])", "⊥"), (r"\\parallel(?![a-zA-Z])", "∥"),
    # Arithmetic
    (r"\\times(?![a-zA-Z])", "×"), (r"\\cdot(?![a-zA-Z])", "·"), (r"\\div(?![a-zA-Z])", "÷"),
    (r"\\pm(?![a-zA-Z])", "±"), (r"\\mp(?![a-zA-Z])", "∓"),
    # Degree / angle
    (r"\\circ(?![a-zA-Z])", "°"), (r"\\angle(?![a-zA-Z])", "∠"),
    # Greek lowercase
    (r"\\alpha(?![a-zA-Z])", "α"), (r"\\beta(?![a-zA-Z])", "β"), (r"\\gamma(?![a-zA-Z])", "γ"),
    (r"\\delta(?![a-zA-Z])", "δ"), (r"\\epsilon(?![a-zA-Z])", "ε"), (r"\\zeta(?![a-zA-Z])", "ζ"),
    (r"\\eta(?![a-zA-Z])", "η"), (r"\\theta(?![a-zA-Z])", "θ"), (r"\\iota(?![a-zA-Z])", "ι"),
    (r"\\kappa(?![a-zA-Z])", "κ"), (r"\\lambda(?![a-zA-Z])", "λ"), (r"\\mu(?![a-zA-Z])", "μ"),
    (r"\\nu(?![a-zA-Z])", "ν"), (r"\\xi(?![a-zA-Z])", "ξ"), (r"\\pi(?![a-zA-Z])", "π"),
    (r"\\rho(?![a-zA-Z])", "ρ"), (r"\\sigma(?![a-zA-Z])", "σ"), (r"\\tau(?![a-zA-Z])", "τ"),
    (r"\\upsilon(?![a-zA-Z])", "υ"), (r"\\phi(?![a-zA-Z])", "φ"), (r"\\chi(?![a-zA-Z])", "χ"),
    (r"\\psi(?![a-zA-Z])", "ψ"), (r"\\omega(?![a-zA-Z])", "ω"),
    # Greek uppercase
    (r"\\Gamma(?![a-zA-Z])", "Γ"), (r"\\Delta(?![a-zA-Z])", "Δ"), (r"\\Theta(?![a-zA-Z])", "Θ"),
    (r"\\Lambda(?![a-zA-Z])", "Λ"), (r"\\Xi(?![a-zA-Z])", "Ξ"), (r"\\Pi(?![a-zA-Z])", "Π"),
    (r"\\Sigma(?![a-zA-Z])", "Σ"), (r"\\Upsilon(?![a-zA-Z])", "Υ"), (r"\\Phi(?![a-zA-Z])", "Φ"),
    (r"\\Psi(?![a-zA-Z])", "Ψ"), (r"\\Omega(?![a-zA-Z])", "Ω"),
    # Calculus
    (r"\\lim(?![a-zA-Z])", "lim"), (r"\\int(?![a-zA-Z])", "∫"), (r"\\sum(?![a-zA-Z])", "∑"),
    (r"\\prod(?![a-zA-Z])", "∏"), (r"\\partial(?![a-zA-Z])", "∂"), (r"\\nabla(?![a-zA-Z])", "∇"),
    (r"\\infty(?![a-zA-Z])", "∞"),
    # Trig / log
    (r"\\sin(?![a-zA-Z])", "sin"), (r"\\cos(?![a-zA-Z])", "cos"), (r"\\tan(?![a-zA-Z])", "tan"),
    (r"\\cot(?![a-zA-Z])", "cot"), (r"\\sec(?![a-zA-Z])", "sec"), (r"\\csc(?![a-zA-Z])", "csc"),
    (r"\\log(?![a-zA-Z])", "log"), (r"\\ln(?![a-zA-Z])", "ln"), (r"\\exp(?![a-zA-Z])", "exp"),
    # Sets / logic
    (r"\\in(?![a-zA-Z])", "∈"), (r"\\notin(?![a-zA-Z])", "∉"), (r"\\subset(?![a-zA-Z])", "⊂"),
    (r"\\cup(?![a-zA-Z])", "∪"), (r"\\cap(?![a-zA-Z])", "∩"), (r"\\forall(?![a-zA-Z])", "∀"),
    (r"\\exists(?![a-zA-Z])", "∃"), (r"\\emptyset(?![a-zA-Z])", "∅"),
    # Misc
    (r"\\ldots(?![a-zA-Z])", "…"), (r"\\cdots(?![a-zA-Z])", "…"), (r"\\triangle(?![a-zA-Z])", "△"),
    (r"\\square(?![a-zA-Z])", "□"), (r"\\because(?![a-zA-Z])", "∵"), (r"\\therefore(?![a-zA-Z])", "∴"),
]


def strip_latex(text) -> str:
    """
    Convert LaTeX markup to readable Unicode plain text for ReportLab PDF.
    Handles multi-segment strings like:
      "$ \\large\\text{For }\\alpha $ $ \\large\\lim_{x \\to 0}\\frac{x^2}{x} $"
    """
    if not text or str(text).strip() in ("", "None", "nan"):
        return ""
    s = str(text).strip()

    # Remove $ delimiters
    s = s.replace("$$", " ").replace("$", " ")
    s = _re.sub(r"\\\(", " ", s)
    s = _re.sub(r"\\\)", " ", s)
    s = _re.sub(r"\\\[", " ", s)
    s = _re.sub(r"\\\]", " ", s)

    # Remove sizing commands
    s = _re.sub(r"\\(large|Large|LARGE|small|tiny|normalsize|huge|Huge)(?![a-zA-Z])\s*", "", s)

    # Unwrap \text{...} (brace-balanced — handles nested groups)
    s = _replace_command(s, "text", 1, lambda g: g)

    # mhchem chemical equations: \ce{H2SO4 -> ...}
    s = _replace_command(s, "ce", 1, _format_chem)

    # Structural math → readable form (brace-balanced, so e.g. \frac{e^{x^2}}{y} works)
    s = _replace_command(s, "frac", 2, _format_frac)
    s = _replace_command(s, "sqrt", 1, lambda g: "√(" + g + ")")
    s = _replace_command(s, "vec", 1, lambda g: "".join(c + "⃗" for c in g))
    s = _replace_command(s, "hat", 1, lambda g: g)
    s = _replace_command(s, "overline", 1, lambda g: g)
    s = _replace_command(s, "underline", 1, lambda g: g)

    # LaTeX commands → Unicode
    for pattern, replacement in _REPLACEMENTS:
        s = _re.sub(pattern, replacement, s)

    # Superscripts / subscripts → real Unicode super/subscript glyphs (brace-balanced)
    s = _replace_scripts(s, "^", _superscript)
    s = _replace_scripts(s, "_", _subscript)

    # Drop remaining \commands and stray braces
    s = _re.sub(r"\\[a-zA-Z]+\*?(?![a-zA-Z])\s*", "", s)
    s = _re.sub(r"[{}]", "", s)

    # Clean whitespace (collapse newlines too — DB text often splits one equation
    # across multiple $...$ segments on separate lines)
    s = _re.sub(r"\s+", " ", s)
    return s.strip()