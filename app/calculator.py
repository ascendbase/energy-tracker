"""
Calculator logic — the energy formulas.
NULL-aware: None means "not entered", 0 means "entered as zero".

Rules (matching frontend behavior):
- FH must be entered (non-None) to calculate anything
- FAC, FIC default to 0 if None
- CD = 0 if TAC or EC is None; CD = (BMR + TAC) - EC only if both non-None
- Other tab vars (B, NM, etc.) default to 0 if None
"""

BMR = 1870

# All user-input variables (order matters for DB schema)
VARIABLES = [
    "fh", "fac", "fic", "tac", "ec",           # Energy tab
    "b", "nm", "trio", "fmf", "sr", "p", "sm"  # Other tab
]

VARIABLE_LABELS = {
    "fh":   "FH (hours)",
    "fac":  "FAC",
    "fic":  "FIC",
    "tac":  "TAC",
    "ec":   "EC",
    "b":    "B",
    "nm":   "NM",
    "trio": "Trio (hours)",
    "fmf":  "FMF",
    "sr":   "SR",
    "p":    "P",
    "sm":   "SM",
}

ENERGY_VARS = ["fh", "fac", "fic", "tac", "ec"]
OTHER_VARS  = ["b", "nm", "trio", "fmf", "sr", "p", "sm"]


def _val(v, default=0.0):
    """Convert None to default, keep numbers as-is."""
    return default if v is None else float(v)


def calculate_from_dict(values: dict) -> dict:
    """
    Calculate from a dict of variable→value.
    None = not entered, number = entered (including 0).

    Rules:
    - FH must be non-None to produce any result
    - FAC, FIC default to 0 if None
    - CD = 0 if TAC or EC is None
    - Other vars default to 0 if None
    """
    fh = values.get("fh")

    # If FH is not entered, nothing to calculate
    if fh is None:
        return {"fs": None, "cd": None, "tes": None, "ts": None}

    fh = float(fh)
    fac = _val(values.get("fac"))
    fic = _val(values.get("fic"))
    tac = values.get("tac")
    ec = values.get("ec")

    # FS always calculates if FH is entered
    fs = (fh * (BMR / 24)) + fac + fic

    # CD = 0 unless both TAC and EC are entered
    if tac is not None and ec is not None:
        cd = (BMR + float(tac)) - float(ec)
    else:
        cd = 0.0

    tes = (fs + cd) / 100

    # Other vars default to 0
    b    = _val(values.get("b"))
    nm   = _val(values.get("nm"))
    trio = _val(values.get("trio"))
    fmf  = _val(values.get("fmf"))
    sr   = _val(values.get("sr"))
    p    = _val(values.get("p"))
    sm   = _val(values.get("sm"))

    ts = tes + b + nm + trio + fmf + sr + p + sm

    return {
        "fs":  round(fs, 2),
        "cd":  round(cd, 2),
        "tes": round(tes, 2),
        "ts":  round(ts, 2),
    }
