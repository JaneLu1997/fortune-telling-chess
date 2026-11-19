"""
Deterministic move-grammar composer (no trigram fields).

Inputs:
- from_square (e.g. "e2")
- to_square   (e.g. "e4")
- piece_type  (one of: "P","N","B","R","Q","K")

Data files (same folder as this script unless you pass paths):
- flux_marks_iching_square_tables.json  (square<->iching<->cast table)
- piece_to_archetype.json               (piece archetype table)
- hexagrams.json                        (1..64 -> {name, keywords[], polarity})

Output:
- structured dict with Theme/Situation/Direction/Tension/Action
"""
import json
from typing import Dict, Any, Optional, List

def load_tone_modifier(path: str = "tone_modifier.json") -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def apply_tone_modifier(
    reading: Dict[str, Any],
    tone_modifier: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Controlled rewrite pass.
    - Uses reading["engine"]["tone"] to select a tone profile.
    - Swaps a few fields deterministically:
        * action_line.verb (optionally)
        * adds adjectives + ending phrase suggestions
    - Leaves symbolic structure unchanged.
    """
    tone = (reading.get("engine", {}) or {}).get("tone", "neutral")
    profile = tone_modifier.get(tone, tone_modifier.get("neutral", {}))

    verb_overrides: List[str] = profile.get("verb_overrides", [])
    adjectives: List[str] = profile.get("adjectives", [])
    endings: List[str] = profile.get("endings", [])

    # Deterministic selection using existing pick_deterministic()
    seed = f"{reading['inputs']['from_square']}->{reading['inputs']['to_square']}|{reading['inputs']['piece_type']}|{tone}"

    chosen_adj = pick_deterministic(adjectives, "adj", seed) if adjectives else None
    chosen_end = pick_deterministic(endings, "end", seed) if endings else None

    # Optionally override verb if tone is affirm/caution.
    # Neutral keeps the original verb to avoid flattening.
    base_verb = (reading.get("action_line", {}) or {}).get("verb")
    if tone in ("affirm", "caution") and verb_overrides:
        chosen_verb = pick_deterministic(verb_overrides, "verb_override", seed)
    else:
        chosen_verb = base_verb

    # Apply minimal edits
    reading_out = dict(reading)
    action_line = dict(reading_out.get("action_line", {}) or {})
    action_line["verb"] = chosen_verb
    reading_out["action_line"] = action_line

    reading_out["style"] = {
        "tone": tone,
        "adjective": chosen_adj,
        "ending": chosen_end,
        "rule": "tone_modifier_swap(action_line.verb)+add(style.adjective,style.ending)"
    }
    return reading_out

import json
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Any, Optional


# -------------------------
# Utilities (deterministic)
# -------------------------

def stable_hash_int(*parts: str, mod: int) -> int:
    s = "|".join(parts).encode("utf-8")
    h = hashlib.sha256(s).hexdigest()
    return int(h[:12], 16) % mod  # deterministic across runs


def pick_deterministic(items: List[str], *seed_parts: str) -> Optional[str]:
    if not items:
        return None
    idx = stable_hash_int(*seed_parts, mod=len(items))
    return items[idx]


def normalize_keywords(xs: List[str]) -> List[str]:
    out = []
    seen = set()
    for x in xs or []:
        k = x.strip().lower()
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out
# -------------------------
# engine_delta_to_tone()
# -------------------------

def to_mover_perspective(eval_cp_white_adv: int, mover_color: str) -> int:
    """
    Convert engine eval (White advantage centipawns)
    into mover perspective.
    """
    if mover_color not in ("w", "b"):
        raise ValueError("mover_color must be 'w' or 'b'")
    return eval_cp_white_adv if mover_color == "w" else -eval_cp_white_adv


def engine_delta_to_tone(delta_cp: int) -> dict:
    """
    Convert centipawn delta into tone classification.
    """

    if delta_cp >= 50:
        tone = "affirm"
    elif delta_cp <= -50:
        tone = "caution"
    else:
        tone = "neutral"

    abs_cp = abs(delta_cp)

    if abs_cp < 50:
        intensity = 0
    elif abs_cp < 150:
        intensity = 1
    elif abs_cp < 300:
        intensity = 2
    else:
        intensity = 3

    labels = []
    if delta_cp >= 150:
        labels.append("strong_gain")
    if delta_cp <= -150:
        labels.append("mistake")
    if delta_cp <= -300:
        labels.append("blunder")

    return {
        "tone": tone,
        "intensity": intensity,
        "labels": labels,
        "delta_cp": delta_cp,
    }
# -------------------------
# Loaders
# -------------------------

def load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


@dataclass(frozen=True)
class Hexagram:
    id: int
    name: str
    keywords: List[str]
    polarity: str  # "expansive" | "contractive" | "balanced"


def load_hexagrams(path: str) -> Dict[int, Hexagram]:
    raw = load_json(path)
    out: Dict[int, Hexagram] = {}
    for k, v in raw.items():
        hid = int(k)
        out[hid] = Hexagram(
            id=hid,
            name=v.get("name", f"Hex {hid}"),
            keywords=normalize_keywords(v.get("keywords", [])),
            polarity=(v.get("polarity", "balanced") or "balanced").strip().lower(),
        )
    return out


def square_to_hex_id(square: str, flux_table: Dict[str, Any]) -> int:
    # Using your existing table: square -> iching number (string)
    ich = flux_table["square_to_iching"][square]  # e.g. "37"
    return int(ich)


# -------------------------
# Core: deterministic composition
# -------------------------

def compute_tension(from_hex: Hexagram, to_hex: Hexagram) -> Dict[str, Any]:
    if from_hex.polarity == to_hex.polarity:
        label = "hold_pattern"
        polarity_shift = "same"
    else:
        polarity_shift = f"{from_hex.polarity}_to_{to_hex.polarity}"
        if from_hex.polarity == "contractive" and to_hex.polarity == "expansive":
            label = "breakthrough"
        elif from_hex.polarity == "expansive" and to_hex.polarity == "contractive":
            label = "compression"
        else:
            label = "rebalancing"

    return {
        "polarity_shift": polarity_shift,
        "label": label,
    }


def filter_verbs_by_direction(archetype_verbs: List[str], direction_keywords: List[str]) -> List[str]:
    """
    Deterministic, rule-based filtering without NLP:
    - If any verb shares a token with any direction keyword -> keep it.
    - Otherwise, return original list.
    """
    verbs = [v.strip() for v in (archetype_verbs or []) if v and v.strip()]
    dkw = set(normalize_keywords(direction_keywords))

    if not dkw or not verbs:
        return verbs

    filtered = []
    for v in verbs:
        vtoks = set(normalize_keywords(v.replace("-", " ").split()))
        if vtoks & dkw:
            filtered.append(v)

    return filtered if filtered else verbs


def compose_reading(
    from_square: str,
    to_square: str,
    piece_type: str,
    mover_color: str,
    eval_before_white_cp: int,
    eval_after_white_cp: int,
    flux_table_path: str = "flux_marks_iching_square_tables.json",
    piece_table_path: str = "piece_to_archetype.json",
    hexagrams_path: str = "hexagrams.json",
) -> Dict[str, Any]:
    """
    Returns a deterministic structured reading:
    - Theme: archetype title
    - Situation: from_hex keywords
    - Direction: to_hex keywords
    - Tension: polarity contrast
    - Action line: chosen verb + chosen direction keyword (deterministic)
    """
    flux = load_json(flux_table_path)
    pieces = load_json(piece_table_path)
    hexes = load_hexagrams(hexagrams_path)

    if piece_type not in pieces:
        raise ValueError(f"Unknown piece_type {piece_type}. Expected one of {list(pieces.keys())}")
    
    if mover_color not in ("w", "b"):
        raise ValueError("mover_color must be 'w' or 'b'")

    from_id = square_to_hex_id(from_square, flux)
    to_id = square_to_hex_id(to_square, flux)

    if from_id not in hexes or to_id not in hexes:
        raise ValueError(f"Missing hexagram definitions for from={from_id} or to={to_id} in {hexagrams_path}")

    from_hex = hexes[from_id]
    to_hex = hexes[to_id]
    arch = pieces[piece_type]

    theme = arch["title"]
    situation_keywords = from_hex.keywords
    direction_keywords = to_hex.keywords

    tension = compute_tension(from_hex, to_hex)

    verbs = arch.get("verbs", [])
    candidate_verbs = filter_verbs_by_direction(verbs, direction_keywords)

    chosen_verb = pick_deterministic(
        candidate_verbs,
        f"verb|{piece_type}",
        f"{from_square}->{to_square}",
        f"from{from_id}",
        f"to{to_id}",
    ) if candidate_verbs else None

    chosen_direction_kw = pick_deterministic(
        direction_keywords,
        f"dirkw|{piece_type}",
        f"{from_square}->{to_square}",
        f"to{to_id}",
    ) if direction_keywords else None

    action_line = {
        "verb": chosen_verb,
        "target_keyword": chosen_direction_kw,
        "rule": "archetype_verb_filtered_by_direction_keywords_then_deterministic_pick",
    }

    # -------------------------
    # Engine modifier layer
    # -------------------------
    eval_before_cp = to_mover_perspective(eval_before_white_cp, mover_color)
    eval_after_cp = to_mover_perspective(eval_after_white_cp, mover_color)
    delta_cp = eval_after_cp - eval_before_cp
    tone_info = engine_delta_to_tone(delta_cp)

    engine_block = {
        **tone_info,
        "mover_color": mover_color,
        "eval_before_cp": eval_before_cp,
        "eval_after_cp": eval_after_cp,
    }

    return {
        "inputs": {
            "from_square": from_square,
            "to_square": to_square,
            "piece_type": piece_type,
            "mover_color": mover_color,
            "eval_before_white_cp": eval_before_white_cp,
            "eval_after_white_cp": eval_after_white_cp,
        },
        "theme": {
            "title": theme,
            "piece_type": piece_type,
        },
        "situation": {
            "hex_id": from_hex.id,
            "hex_name": from_hex.name,
            "keywords": situation_keywords,
            "polarity": from_hex.polarity,
        },
        "direction": {
            "hex_id": to_hex.id,
            "hex_name": to_hex.name,
            "keywords": direction_keywords,
            "polarity": to_hex.polarity,
        },
        "tension": tension,
        "action_line": action_line,
        "engine": engine_block,
    }

if __name__ == "__main__":
    reading = compose_reading(
        "g1", "f3", "N",
        mover_color="w",
        eval_before_white_cp=20,
        eval_after_white_cp=90
    )

    tone_mod = load_tone_modifier("tone_modifier.json")
    reading = apply_tone_modifier(reading, tone_mod)

    print(json.dumps(reading, ensure_ascii=False, indent=2))