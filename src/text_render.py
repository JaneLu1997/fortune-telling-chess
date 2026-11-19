from typing import List, Dict, Any

def _build_common_fields(reading: Dict[str, Any]) -> dict:
    theme = reading["theme"]["title"]

    sit = reading["situation"]
    direc = reading["direction"]
    tension = reading["tension"]["label"]

    engine = reading.get("engine", {})
    tone = engine.get("tone", "neutral")
    intensity = engine.get("intensity", 0)

    style = reading.get("style", {})
    adj = style.get("adjective")
    ending = style.get("ending")

    action = reading.get("action_line", {})
    verb = action.get("verb")
    target_kw = action.get("target_keyword")

    sit_kw = sit["keywords"][0] if sit.get("keywords") else None
    dir_kw = direc["keywords"][0] if direc.get("keywords") else None

    tone_phrase = {
        "affirm": "Press the advantage.",
        "neutral": "Hold your shape.",
        "caution": "Reduce exposure."
    }.get(tone, "Hold your shape.")

    intensity_phrase = {
        0: "",
        1: "The signal is noticeable.",
        2: "The signal is strong.",
        3: "The signal is decisive."
    }.get(intensity, "")

    tension_phrase = {
        "hold_pattern": "Nothing needs to change—only execution.",
        "breakthrough": "A locked door opens if you shift posture.",
        "compression": "Power is leaking; tighten the system before expanding again.",
        "rebalancing": "Bring force back into proportion."
    }.get(tension, "Rebalance before you escalate.")

    return {
        "theme": theme,
        "adj": adj,
        "ending": ending,
        "sit_kw": sit_kw,
        "dir_kw": dir_kw,
        "verb": verb,
        "target_kw": target_kw,
        "tone_phrase": tone_phrase,
        "intensity_phrase": intensity_phrase,
        "tension_phrase": tension_phrase,
    }


def render_reading_text_full(reading: Dict[str, Any]) -> str:
    """
    Full reading for final slip:
    keeps archetype, tension line, ending, and intensity.
    """
    f = _build_common_fields(reading)
    lines = []

    lines.append(f"{f['theme']} — {f['adj']}." if f["adj"] else f"{f['theme']}.")

    if f["sit_kw"] and f["dir_kw"]:
        lines.append(f"You move from {f['sit_kw']} toward {f['dir_kw']}.")
    elif f["sit_kw"]:
        lines.append(f"You move from {f['sit_kw']}.")
    elif f["dir_kw"]:
        lines.append(f"You move toward {f['dir_kw']}.")

    lines.append(f["tension_phrase"])

    if f["verb"] and f["target_kw"]:
        lines.append(f"Action: {f['verb']} through {f['target_kw']}.")
    elif f["verb"]:
        lines.append(f"Action: {f['verb']}.")

    lines.append(f["tone_phrase"])

    if f["ending"]:
        lines.append(f"Ending: {f['ending']}.")

    if f["intensity_phrase"]:
        lines.append(f["intensity_phrase"])

    return "\n".join([ln for ln in lines if ln])


def render_reading_text_event(reading: Dict[str, Any]) -> str:
    """
    Short reading for per-move event slips:
    no archetype, no tension line, no ending, no intensity.
    """
    f = _build_common_fields(reading)
    lines = []

    if f["adj"]:
        lines.append(f"{f['adj'].capitalize()}.")

    if f["sit_kw"] and f["dir_kw"]:
        lines.append(f"You move from {f['sit_kw']} toward {f['dir_kw']}.")
    elif f["sit_kw"]:
        lines.append(f"You move from {f['sit_kw']}.")
    elif f["dir_kw"]:
        lines.append(f"You move toward {f['dir_kw']}.")

    if f["verb"] and f["target_kw"]:
        lines.append(f"Action: {f['verb']} through {f['target_kw']}.")
    elif f["verb"]:
        lines.append(f"Action: {f['verb']}.")

    lines.append(f["tone_phrase"])

    return "\n".join([ln for ln in lines if ln])


# backward-compatible alias if anything else still imports render_reading_text
def render_reading_text(reading: Dict[str, Any]) -> str:
    return render_reading_text_full(reading)


def render_reading_triad(readings: List[Dict[str, Any]]) -> str:
    if len(readings) != 3:
        raise ValueError("render_reading_triad expects exactly 3 readings")

    past, present, future = readings

    parts = []
    parts.append("PAST")
    parts.append(render_reading_text_full(past))
    parts.append("")
    parts.append("PRESENT")
    parts.append(render_reading_text_full(present))
    parts.append("")
    parts.append("FUTURE")
    parts.append(render_reading_text_full(future))

    return "\n".join(parts)