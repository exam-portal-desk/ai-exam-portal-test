"""
app/utils/latex.py
Strips LaTeX markup from question/option text for ReportLab PDF rendering.
Moved from admin.py where it was defined at module level.
"""

import re as _re

_REPLACEMENTS = [
    # Arrows
    (r"\\rightarrow\b", "→"), (r"\\leftarrow\b", "←"),
    (r"\\Rightarrow\b", "⇒"), (r"\\Leftarrow\b", "⇐"),
    (r"\\to\b", "→"),
    # Relations
    (r"\\leq\b", "≤"), (r"\\geq\b", "≥"), (r"\\neq\b", "≠"),
    (r"\\approx\b", "≈"), (r"\\equiv\b", "≡"), (r"\\sim\b", "~"),
    (r"\\propto\b", "∝"), (r"\\perp\b", "⊥"), (r"\\parallel\b", "∥"),
    # Arithmetic
    (r"\\times\b", "×"), (r"\\cdot\b", "·"), (r"\\div\b", "÷"),
    (r"\\pm\b", "±"), (r"\\mp\b", "∓"),
    # Degree / angle
    (r"\\circ\b", "°"), (r"\\angle\b", "∠"),
    # Greek lowercase
    (r"\\alpha\b", "α"), (r"\\beta\b", "β"), (r"\\gamma\b", "γ"),
    (r"\\delta\b", "δ"), (r"\\epsilon\b", "ε"), (r"\\zeta\b", "ζ"),
    (r"\\eta\b", "η"), (r"\\theta\b", "θ"), (r"\\iota\b", "ι"),
    (r"\\kappa\b", "κ"), (r"\\lambda\b", "λ"), (r"\\mu\b", "μ"),
    (r"\\nu\b", "ν"), (r"\\xi\b", "ξ"), (r"\\pi\b", "π"),
    (r"\\rho\b", "ρ"), (r"\\sigma\b", "σ"), (r"\\tau\b", "τ"),
    (r"\\upsilon\b", "υ"), (r"\\phi\b", "φ"), (r"\\chi\b", "χ"),
    (r"\\psi\b", "ψ"), (r"\\omega\b", "ω"),
    # Greek uppercase
    (r"\\Gamma\b", "Γ"), (r"\\Delta\b", "Δ"), (r"\\Theta\b", "Θ"),
    (r"\\Lambda\b", "Λ"), (r"\\Xi\b", "Ξ"), (r"\\Pi\b", "Π"),
    (r"\\Sigma\b", "Σ"), (r"\\Upsilon\b", "Υ"), (r"\\Phi\b", "Φ"),
    (r"\\Psi\b", "Ψ"), (r"\\Omega\b", "Ω"),
    # Calculus
    (r"\\lim\b", "lim"), (r"\\int\b", "∫"), (r"\\sum\b", "∑"),
    (r"\\prod\b", "∏"), (r"\\partial\b", "∂"), (r"\\nabla\b", "∇"),
    (r"\\infty\b", "∞"),
    # Trig / log
    (r"\\sin\b", "sin"), (r"\\cos\b", "cos"), (r"\\tan\b", "tan"),
    (r"\\cot\b", "cot"), (r"\\sec\b", "sec"), (r"\\csc\b", "csc"),
    (r"\\log\b", "log"), (r"\\ln\b", "ln"), (r"\\exp\b", "exp"),
    # Sets / logic
    (r"\\in\b", "∈"), (r"\\notin\b", "∉"), (r"\\subset\b", "⊂"),
    (r"\\cup\b", "∪"), (r"\\cap\b", "∩"), (r"\\forall\b", "∀"),
    (r"\\exists\b", "∃"), (r"\\emptyset\b", "∅"),
    # Misc
    (r"\\ldots\b", "…"), (r"\\cdots\b", "…"), (r"\\triangle\b", "△"),
    (r"\\square\b", "□"), (r"\\because\b", "∵"), (r"\\therefore\b", "∴"),
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
    s = _re.sub(r"\\(large|Large|LARGE|small|tiny|normalsize|huge|Huge)\b\s*", "", s)

    # Unwrap \text{...}
    for _ in range(6):
        s = _re.sub(r"\\text\{([^}]*)\}", r"\1", s)

    # Structural math → readable form
    s = _re.sub(r"\\frac\{([^}]+)\}\{([^}]+)\}", r"(\1)/(\2)", s)
    s = _re.sub(r"\\sqrt\{([^}]+)\}", r"√(\1)", s)
    s = _re.sub(r"\\vec\{([^}]+)\}", r"\1", s)
    s = _re.sub(r"\\hat\{([^}]+)\}", r"\1", s)
    s = _re.sub(r"\\overline\{([^}]+)\}", r"\1", s)
    s = _re.sub(r"\\underline\{([^}]+)\}", r"\1", s)

    # LaTeX commands → Unicode
    for pattern, replacement in _REPLACEMENTS:
        s = _re.sub(pattern, replacement, s)

    # Superscripts / subscripts
    s = _re.sub(r"\^\{([^}]+)\}", r"^\1", s)
    s = _re.sub(r"_\{([^}]+)\}", r"_\1", s)
    s = _re.sub(r"\^([^\{])", r"^\1", s)
    s = _re.sub(r"_([^\{ ])", r"_\1", s)

    # Drop remaining \commands and stray braces
    s = _re.sub(r"\\[a-zA-Z]+\*?\b\s*", "", s)
    s = _re.sub(r"[{}]", "", s)

    # Clean whitespace
    s = _re.sub(r"[ \t]+", " ", s)
    return s.strip()