# src/mebot.py, this version contains board, compas and OLED screen
import json
import urllib.request
import math
import chess
import chess.pgn
import chess.engine
import pandas as pd
import random
from pathlib import Path

# NEW: serial
import time
try:
    import serial
except ImportError:
    serial = None  # allows script to run without pyserial

# --- reading engine imports ---
from move_grammar import compose_reading, apply_tone_modifier, load_tone_modifier

from text_render import render_reading_text_event, render_reading_text_full

# --- config ---
PI_PRINTER_URL = "http://172.16.78.99:5000/print"
ENGINE_PATH = "～/engines/stockfish/stockfish"
BOOK_CSV    = "～/data/processed/you_book_moves.csv"
RAW_PGN_DIR = Path("～/data/raw_pgn")
YOUR_HANDLES = {"JadedPineapple7", "SashaLyn256"}  # used in PGN demo to detect your turns

# Big board
LED_SERIAL_PORT = ""
LED_BAUD = 115200
USE_LED = True

# Compass 2.0
COMPASS_SERIAL_PORT = ""   # change to actual compass port
COMPASS_BAUD = 115200
USE_COMPASS = True

# --- load your You-Book (FEN -> [(uci, prob), ...]) ---
book_df = pd.read_csv(BOOK_CSV)
book = {
    fen: list(zip(g["uci"].tolist(), g["prob"].tolist()))
    for fen, g in book_df.groupby("fen_before")
}

engine = chess.engine.SimpleEngine.popen_uci(ENGINE_PATH)

# NEW: open serial (best-effort; don’t crash if missing)
led_ser = None
if USE_LED and serial is not None:
    try:
        led_ser = serial.Serial(LED_SERIAL_PORT, LED_BAUD, timeout=1)
        time.sleep(2)  # give ESP32 time to reset
        print(f"[LED] Connected on {LED_SERIAL_PORT} @ {LED_BAUD}")
    except Exception as e:
        print(f"[LED] Could not open serial: {e}")
        led_ser = None

compass_ser = None
if USE_COMPASS and serial is not None:
    try:
        compass_ser = serial.Serial(COMPASS_SERIAL_PORT, COMPASS_BAUD, timeout=1)
        time.sleep(2)
        print(f"[COMPASS] Connected on {COMPASS_SERIAL_PORT} @ {COMPASS_BAUD}")
    except Exception as e:
        print(f"[COMPASS] Could not open serial: {e}")
        compass_ser = None
# -----------------------------
# LED helpers
# -----------------------------
def send_move_to_led(move: chess.Move):
    """
    Send a bot move to Arduino as:
    MOVE e2 e4
    """
    if led_ser is None:
        return
    try:
        from_sq = chess.square_name(move.from_square)
        to_sq = chess.square_name(move.to_square)
        msg = f"MOVE {from_sq} {to_sq}\n".encode("utf-8")
        led_ser.write(msg)
        led_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[LED] move send error: {e}")


def send_field_to_led(squares):
    """
    Send a danger field to Arduino as:
    FIELD a3 b4 e5
    """
    if led_ser is None:
        return
    try:
        if not squares:
            led_ser.write(b"CLR\n")
            led_ser.flush()
            return
        payload = " ".join(squares)
        msg = f"FIELD {payload}\n".encode("utf-8")
        led_ser.write(msg)
        led_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[LED] field send error: {e}")

def send_check_to_led():
    """
    Big board protocol:
    CHECK = blink all 64 LEDs
    """
    if led_ser is None:
        return
    try:
        led_ser.write(b"CHECK\n")
        led_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[LED] check send error: {e}")


def send_mate_to_led():
    """
    Big board protocol:
    MATE = all 64 LEDs solid on
    """
    if led_ser is None:
        return
    try:
        led_ser.write(b"MATE\n")
        led_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[LED] mate send error: {e}")

def clear_compass():
    """
    Compass protocol:
    single byte C = clear
    """
    if compass_ser is None:
        return
    try:
        compass_ser.write(b"C")
        compass_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[COMPASS] clear error: {e}")


def send_move_to_compass(move: chess.Move):
    """
    Compass protocol expects exactly 4 ASCII chars like e2e4
    """
    if compass_ser is None:
        return
    try:
        payload = move.uci()[:4].encode("ascii")
        compass_ser.write(payload)
        compass_ser.flush()
        time.sleep(0.02)
    except Exception as e:
        print(f"[COMPASS] move send error: {e}")


def engine_best_move(board: chess.Board, depth: int = 10) -> chess.Move:
    """
    Pure engine suggestion for the player's next move.
    Separate from your hybrid bot logic.
    """
    info = engine.analyse(board, limit=chess.engine.Limit(depth=depth))
    return info["pv"][0]


def suggest_player_move_to_compass(board: chess.Board):
    """
    When it becomes the player's turn, compute best move and show it on compass.
    """
    try:
        mv = engine_best_move(board, depth=10)
        send_move_to_compass(mv)
        print(f"[COMPASS] suggests: {mv.uci()}")
        return mv
    except Exception as e:
        print(f"[COMPASS] suggestion error: {e}")
        return None

# -----------------------------
# Danger mapping helpers
# -----------------------------
def _step_toward(a: int, b: int) -> int:
    if b > a:
        return 1
    if b < a:
        return -1
    return 0


def _full_line_through(attacker_sq: int, target_sq: int):
    """
    For sliding attacks, return the full file/rank/diagonal line
    passing through attacker and target, extending to board edges.
    """
    ax = chess.square_file(attacker_sq)
    ay = chess.square_rank(attacker_sq)
    tx = chess.square_file(target_sq)
    ty = chess.square_rank(target_sq)

    dx = _step_toward(ax, tx)
    dy = _step_toward(ay, ty)

    # not aligned
    if not (
        ax == tx or
        ay == ty or
        abs(ax - tx) == abs(ay - ty)
    ):
        return set()

    if dx == 0 and dy == 0:
        return {attacker_sq}

    squares = set()

    # walk backward to edge
    x, y = ax, ay
    while 0 <= x < 8 and 0 <= y < 8:
        squares.add(chess.square(x, y))
        x -= dx
        y -= dy

    # walk forward to edge
    x, y = ax + dx, ay + dy
    while 0 <= x < 8 and 0 <= y < 8:
        squares.add(chess.square(x, y))
        x += dx
        y += dy

    return squares


def _attack_visual_squares(board: chess.Board, attacker_sq: int, target_sq: int):
    """
    Return squares to light for one actual attack:
    - always include attacker square
    - always include attacked piece square
    - for bishop/rook/queen, include the full attack line
    - for knight/pawn/king, just attacker + target
    """
    piece = board.piece_at(attacker_sq)
    if piece is None:
        return set()

    out = {attacker_sq, target_sq}

    if piece.piece_type in (chess.BISHOP, chess.ROOK, chess.QUEEN):
        out |= _full_line_through(attacker_sq, target_sq)

    return out


def compute_current_attack_map(board: chess.Board, player_color: bool):
    """
    After bot has moved, compute:
    1) which player pieces are currently attacked
    2) visual attack-line squares for those attacks

    Returns:
    {
        "vulnerable_piece_squares": [...],
        "attack_line_squares": [...],
        "combined": [...]
    }
    """
    bot_color = not player_color

    vulnerable_piece_squares = set()
    attack_line_squares = set()

    for target_sq in chess.SQUARES:
        target_piece = board.piece_at(target_sq)
        if not target_piece or target_piece.color != player_color:
            continue

        attackers = board.attackers(bot_color, target_sq)
        if not attackers:
            continue

        vulnerable_piece_squares.add(target_sq)

        for attacker_sq in attackers:
            attack_line_squares |= _attack_visual_squares(board, attacker_sq, target_sq)

    combined = vulnerable_piece_squares | attack_line_squares

    return {
        "vulnerable_piece_squares": sorted(chess.square_name(sq) for sq in vulnerable_piece_squares),
        "attack_line_squares": sorted(chess.square_name(sq) for sq in attack_line_squares),
        "combined": sorted(chess.square_name(sq) for sq in combined),
    }


def compute_danger_field(board: chess.Board, player_color: bool, bot_color: bool):
    """
    Danger display after bot move:
    - actual check / mate state
    - attacked player pieces
    - visual attack lines of the attacking pieces

    No engine continuation forecast.
    """
    check_to_player = board.is_check() and board.turn == player_color
    mate_to_player = board.is_checkmate() and board.turn == player_color

    attack_map = compute_current_attack_map(board, player_color)

    return {
        "check_to_player": check_to_player,
        "mate_to_player": mate_to_player,
        "vulnerable_piece_squares": attack_map["vulnerable_piece_squares"],
        "attack_line_squares": attack_map["attack_line_squares"],
        "combined": attack_map["combined"],
    }


# -----------------------------
# Engine / eval helpers
# -----------------------------
def score_to_white_cp(score: chess.engine.PovScore) -> int:
    """
    Convert engine score to centipawns from White's advantage perspective.
    Mate is mapped to a large cp value so delta logic stays stable.
    """
    s = score.white()
    mate = s.mate()
    if mate is not None:
        return 100000 if mate > 0 else -100000
    cp = s.score()
    return int(cp) if cp is not None else 0


def eval_white_cp(board: chess.Board, think_ms: int = 80) -> int:
    """
    Evaluate current position with Stockfish, returning White-advantage centipawns.
    Uses the global `engine` already opened in this script.
    """
    info = engine.analyse(board, limit=chess.engine.Limit(time=think_ms / 1000.0))
    return score_to_white_cp(info["score"])


# -----------------------------
# New move-feedback helpers
# -----------------------------
def to_mover_perspective(eval_cp_white_adv: int, mover_color: str) -> int:
    """
    Convert White-advantage centipawns into mover perspective.
    Positive means better for the side that just moved.
    """
    if mover_color not in ("w", "b"):
        raise ValueError("mover_color must be 'w' or 'b'")
    return eval_cp_white_adv if mover_color == "w" else -eval_cp_white_adv


def cp_to_win_probability(eval_cp_from_player_perspective: int) -> float:
    """
    Approximate win probability from centipawn eval.
    Heuristic only, meant for readable feedback.

    Handles mate-mapped extreme scores safely.
    """
    cp = int(eval_cp_from_player_perspective)

    if cp >= 10000:
        return 100.0
    if cp <= -10000:
        return 0.0

    x = cp / 100.0
    prob = 1.0 / (1.0 + math.exp(-0.8 * x))
    return round(prob * 100, 1)


def describe_position_change(delta_cp: int) -> str:
    if delta_cp >= 100:
        return "significantly improved your position"
    elif delta_cp >= 30:
        return "improved your position"
    elif delta_cp > -30:
        return "kept your position about the same"
    elif delta_cp > -100:
        return "slightly worsened your position"
    else:
        return "worsened your position"


def build_move_feedback(eval_before_white_cp: int, eval_after_white_cp: int, mover_color: str) -> dict:
    """
    Human-readable feedback for the side that just moved.
    """
    eval_before_cp = to_mover_perspective(eval_before_white_cp, mover_color)
    eval_after_cp = to_mover_perspective(eval_after_white_cp, mover_color)
    delta_cp = eval_after_cp - eval_before_cp

    return {
        "position_change_text": describe_position_change(delta_cp),
        "win_probability_percent": cp_to_win_probability(eval_after_cp),
        "eval_before_cp": eval_before_cp,
        "eval_after_cp": eval_after_cp,
        "delta_cp": delta_cp,
    }

# -----------------------------
# move event classifier
# -----------------------------
def detect_sacrifice(board_before: chess.Board, move: chess.Move) -> bool:
    """
    Simple sacrifice signal:
    mover gives up material immediately on the destination square.
    This is intentionally rough, not a full tactical proof.
    """
    piece = board_before.piece_at(move.from_square)
    if piece is None:
        return False

    mover_value = {
        chess.PAWN: 1,
        chess.KNIGHT: 3,
        chess.BISHOP: 3,
        chess.ROOK: 5,
        chess.QUEEN: 9,
        chess.KING: 100,
    }.get(piece.piece_type, 0)

    board_after = board_before.copy(stack=False)
    board_after.push(move)

    to_sq = move.to_square
    attackers = board_after.attackers(not piece.color, to_sq)
    defenders = board_after.attackers(piece.color, to_sq)

    # rough "hanging or underdefended valuable piece" condition
    if mover_value >= 3 and len(attackers) > len(defenders):
        return True

    return False

from typing import Optional

def classify_notable_move(board_before: chess.Board, move: chess.Move, reading_one: dict) -> Optional[str]:
    """
    Return one of:
    brilliant, great, miss, blunder, sacrifice
    or None if not notable enough.
    """
    feedback = reading_one["move_feedback"]
    delta_cp = feedback["delta_cp"]

    # strong negative events
    if delta_cp <= -250:
        return "blunder"
    if delta_cp <= -100:
        return "miss"

    # sacrifice-like positive event
    if detect_sacrifice(board_before, move) and delta_cp >= 80:
        return "brilliant"

    # strong positive events
    if delta_cp >= 180:
        return "great"

    return None

def classify_move_event(followed_compass: bool, move_label: Optional[str]) -> str:
    """
    Return one of:
    c = ignored compass + significant
    a = followed compass + significant
    d = ignored compass + insignificant
    b = followed compass + insignificant
    """
    significant = move_label is not None

    if not followed_compass and significant:
        return "c"
    if followed_compass and significant:
        return "a"
    if not followed_compass and not significant:
        return "d"
    return "b"

EVENT_PRIORITY = {"c": 4, "a": 3, "d": 2, "b": 1}


def should_print_reading(followed_compass: bool, move_label: Optional[str]) -> bool:
    """
    Print only when:
    1) player ignored compass suggestion
    2) move is notable
    """
    if not followed_compass:
        return True
    if move_label is not None:
        return True
    return False


def render_single_event_reading(reading_one: dict, followed_compass: bool, move_label: Optional[str]) -> str:
    """
    One-move receipt instead of 3-move triad.
    """
    board_info = reading_one["board"]
    feedback = reading_one["move_feedback"]

    reason_bits = []
    if not followed_compass:
        reason_bits.append("ignored the compass")
    if move_label is not None:
        reason_bits.append(f"move classified as {move_label}")

    reason_line = ", ".join(reason_bits) if reason_bits else "move registered"

    lines = [
        "=== CMA EVENT READING ===",
        f"Move: {board_info['uci_move']}",
        f"Piece: {board_info['piece_type']}",
        f"Trigger: {reason_line}",
        "",
        f"Theme: {reading_one.get('theme', '')}",
        f"Position signal: {feedback['position_change_text']}",
        f"Win probability: {feedback['win_probability_percent']}%",
    ]

    # keep using whatever compose_reading already gave you
    if "situation" in reading_one and isinstance(reading_one["situation"], dict):
        lines.append(f"Situation: {reading_one['situation'].get('name', '')}")
    if "direction" in reading_one and isinstance(reading_one["direction"], dict):
        lines.append(f"Direction: {reading_one['direction'].get('name', '')}")
    if "action" in reading_one:
        lines.append(f"Action: {reading_one['action']}")

    return "\n".join(lines)

# -----------------------------
# printer set up
# -----------------------------
def send_to_printer(text: str):
    """
    Send plain text to the Pi printer server.
    """
    try:
        payload = json.dumps({"text": text}).encode("utf-8")
        req = urllib.request.Request(
            PI_PRINTER_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            print(f"[PRINTER] sent, status={resp.status}")
    except Exception as e:
        print(f"[PRINTER] send error: {e}")

# -----------------------------
# add board position on prints
# -----------------------------
def render_board_ascii(board: chess.Board) -> str:
    """
    Render board as 8 text rows, top (rank 8) to bottom (rank 1),
    using '.' for empty squares.
    """
    rows = []
    for rank in range(7, -1, -1):  # 8 down to 1
        cells = []
        for file in range(8):       # a to h
            sq = chess.square(file, rank)
            piece = board.piece_at(sq)
            cells.append(piece.symbol() if piece else ".")
        rows.append(" ".join(cells))
    return "\n".join(rows)
# -----------------------------
# Reading generation
# -----------------------------
def generate_reading_for_move(
    board_before: chess.Board,
    move: chess.Move,
    eval_before_white_cp: int,
    eval_after_white_cp: int,
    tone_mod: dict,
) -> dict:
    """
    Create semantic reading for a single move.
    - mover_color is from board_before.turn (side to move before pushing)
    - piece_type is determined from board_before at move.from_square
    """
    mover_color = "w" if board_before.turn == chess.WHITE else "b"

    piece = board_before.piece_at(move.from_square)
    if piece is None:
        raise ValueError("No piece on from_square in board_before")

    piece_type = piece.symbol().upper()  # P N B R Q K
    from_square = chess.square_name(move.from_square)
    to_square = chess.square_name(move.to_square)

    reading = compose_reading(
        from_square=from_square,
        to_square=to_square,
        piece_type=piece_type,
        mover_color=mover_color,
        eval_before_white_cp=eval_before_white_cp,
        eval_after_white_cp=eval_after_white_cp,
    )

    # Apply tone rewrite layer if available
    reading = apply_tone_modifier(reading, tone_mod)

    # Add human-readable move feedback
    reading["move_feedback"] = build_move_feedback(
        eval_before_white_cp=eval_before_white_cp,
        eval_after_white_cp=eval_after_white_cp,
        mover_color=mover_color,
    )

    # Attach context (helps debugging)
    reading["board"] = {
        "fen_before": board_before.fen(),
        "uci_move": move.uci(),
        "from_square": from_square,
        "to_square": to_square,
        "piece_type": piece_type,
        "mover_color": mover_color,
        "eval_before_white_cp": eval_before_white_cp,
        "eval_after_white_cp": eval_after_white_cp,
    }
    return reading


# -----------------------------
# Bot move selection
# -----------------------------
def you_move(board: chess.Board, depth=8) -> chess.Move:
    """Pick your move: book first, else Stockfish best."""
    fen = board.fen()
    if fen in book:
        bag = book[fen]
        r, cum = random.random(), 0.0
        for uci, p in bag:
            cum += p
            if r <= cum:
                return chess.Move.from_uci(uci)
        return chess.Move.from_uci(bag[0][0])  # safety

    info = engine.analyse(board, limit=chess.engine.Limit(depth=depth))
    return info["pv"][0]

# -----------------------------
# OLED screen display
# -----------------------------
def send_oled_status(board: chess.Board, position_change_text: str, eval_after_cp: int, win_probability_percent: float):
    if compass_ser is None:
        return

    try:
        t = position_change_text.lower()

        if "significantly improved" in t or "improved" in t:
            arrow = "UP"
        elif "worsened" in t:
            arrow = "DN"
        else:
            arrow = "EQ"

        line1 = f"POS: {arrow}"

        # real board-state override
        if board.is_checkmate():
            line2 = "GAME OVER, YOU LOST :("
            line3 = ""
        elif board.is_check():
            line2 = "CHECK"
            line3 = f"WIN:{win_probability_percent:.1f}%"
        else:
            eval_pawns = round(eval_after_cp / 100.0, 1)
            line2 = f"{eval_pawns:+.1f}"
            line3 = f"WIN:{win_probability_percent:.1f}%"

        msg = f"OLED2 {line1}|{line2}|{line3}\n".encode("ascii", errors="ignore")
        compass_ser.write(msg)
        compass_ser.flush()
        time.sleep(0.02)

    except Exception as e:
        print(f"[COMPASS/OLED] status send error: {e}")

# ---------- Mode 1: Interactive play in Terminal ----------
from typing import Optional

def parse_user_move(board: chess.Board, s: str) -> Optional[chess.Move]:
    s = s.strip()
    # SAN first
    try:
        mv = board.parse_san(s)
        if mv in board.legal_moves:
            return mv
    except Exception:
        pass
    # UCI fallback
    try:
        mv = chess.Move.from_uci(s)
        if mv in board.legal_moves:
            return mv
    except Exception:
        pass
    return None

# -----------------------------
# Summary slip helper
# -----------------------------

# ---------- summary slip fixed part [PGN, date and time, result etc]----------

from datetime import datetime

def game_result_label(board: chess.Board, end_reason: Optional[str]) -> str:
    if end_reason == "resigned":
        return "Resigned"

    if board.is_checkmate():
        return "Checkmate"

    if board.is_stalemate():
        return "Draw by stalemate"

    if board.is_insufficient_material():
        return "Draw by insufficient material"

    if board.is_seventyfive_moves():
        return "Draw by 75-move rule"

    if board.is_fivefold_repetition():
        return "Draw by repetition"

    if board.is_game_over():
        return f"Game over ({board.result()})"

    return "Game stopped"


# ---------- summary selection helper----------

def summarize_event_classes(player_event_log: list[dict]) -> dict:
    counts = {"a": 0, "b": 0, "c": 0, "d": 0}
    for item in player_event_log:
        counts[item["event_class"]] += 1

    total = len(player_event_log)
    dominant = max(counts, key=counts.get) if total else "b"

    ranked_events = sorted(
        player_event_log,
        key=lambda x: EVENT_PRIORITY[x["event_class"]],
        reverse=True
    )

    return {
        "counts": counts,
        "dominant": dominant,
        "ranked_events": ranked_events,
        "total": total,
    }

# ---------- final summary header renderer----------
def render_summary_header(game: chess.pgn.Game, board: chess.Board, end_reason: Optional[str]) -> str:
    now = datetime.now()
    exporter = chess.pgn.StringExporter(headers=False, variations=False, comments=False)
    pgn_text = game.accept(exporter).strip()

    lines = [
        "=== CMA FINAL SUMMARY ===",
        f"Date: {now.strftime('%Y-%m-%d')}",
        f"Time: {now.strftime('%H:%M:%S')}",
        f"Result: {game_result_label(board, end_reason)}",
        f"Score: {board.result() if board.is_game_over() else '*'}",
        "",
        "PGN:",
        pgn_text if pgn_text else "(no moves recorded)",
    ]
    return "\n".join(lines)


# ---------- final summary body stub----------
def render_final_summary_body(player_event_log: list[dict]) -> str:
    summary = summarize_event_classes(player_event_log)
    ranked_events = summary["ranked_events"]

    if not ranked_events:
        return (
            "FORTUNE SUMMARY\n\n"
            "No dominant signal emerged.\n"
            "Your game remained before declaration: uncast, unresolved, still gathering form.\n\n"
            "Going forward: do not force a prophecy from silence. Wait until the field answers."
        )

    top_event = ranked_events[0]
    top_reading = top_event["reading"]

    piece_type = top_reading["theme"]["piece_type"]

    archetypes = {
        "P": {
            "title": "Orphan",
            "gift": "You gather power through endurance, repetition, and small survivals.",
            "shadow": "You may confuse safety with staying small.",
            "advice": "Advance one square honestly. Let accumulation become direction."
        },
        "R": {
            "title": "Warrior",
            "gift": "You create structure under pressure and make the field legible.",
            "shadow": "You may defend the perimeter after the real crisis has already entered.",
            "advice": "Hold the line, but do not worship the wall."
        },
        "B": {
            "title": "Magician",
            "gift": "You understand the hidden angle. You change meaning without touching the center.",
            "shadow": "You may reframe so fluently that direct contact becomes avoidable.",
            "advice": "Use interpretation as a blade, not as fog."
        },
        "N": {
            "title": "Wanderer",
            "gift": "You break the model by refusing its geometry.",
            "shadow": "You may mistake restlessness for strategy.",
            "advice": "Arrive unpredictably, but know what you came to alter."
        },
        "Q": {
            "title": "Ruler",
            "gift": "You see the whole field and move with command.",
            "shadow": "You may collapse complexity into the force of your own will.",
            "advice": "Command less from speed, more from proportion."
        },
        "K": {
            "title": "Altruist",
            "gift": "You preserve continuity when the system strains.",
            "shadow": "You may carry too much in the name of keeping things alive.",
            "advice": "Protect the core, but do not become the cage around it."
        },
    }

    arch = archetypes.get(piece_type, {
        "title": "Unknown",
        "gift": "Your pattern resisted classification.",
        "shadow": "Your signal moved outside the known grammar.",
        "advice": "Read the anomaly before naming it."
    })

    move_line = f"{top_event['san']} ({top_event['uci']})"
    move_label = top_event["move_label"] or "quiet deviation"
    followed = "followed the compass" if top_event["followed_compass"] else "refused the compass"

    symbolic_reading = render_reading_text_full(top_reading)

    lines = [
        "FORTUNE SUMMARY",
        "",
        f"ARCHETYPE: {arch['title']}",
        f"Anchor move: {move_line}",
        f"Signal: {move_label}; you {followed}.",
        "",
        "WHAT THIS REVEALS",
        arch["gift"],
        arch["shadow"],
        "",
        "FIELD READING",
        symbolic_reading,
        "",
        "GOING FORWARD",
        arch["advice"],
    ]

    return "\n".join(lines)

# ---------- final slip renderer----------
def render_final_summary_slip(game: chess.pgn.Game, board: chess.Board, end_reason: Optional[str], player_event_log: list[dict]) -> str:
    header = render_summary_header(game, board, end_reason)
    body = render_final_summary_body(player_event_log)
    return f"{header}\n\n{body}"

def play_interactive():
    print("=== mebot interactive ===")
    color = input("Play as (w/b)? ").strip().lower()
    assert color in {"w", "b"}, "Type w or b"
    you_are_white = (color == "w")

    board = chess.Board()
    game = chess.pgn.Game()
    node = game

    tone_mod = load_tone_modifier("tone_modifier.json")

    # latest compass-suggested human move
    pending_player_suggestion = None

    player_event_log = []
    game_end_reason = None

    try:
        while not board.is_game_over():
            is_human_turn = (board.turn and you_are_white) or ((not board.turn) and (not you_are_white))

            if is_human_turn:
                print(board)

                if pending_player_suggestion is not None:
                    print(f"Compass suggested: {pending_player_suggestion.uci()}")
                    print("Type Y if player followed the compass move.")
                    print("Otherwise enter player's actual move (SAN like e4/Nf3 or UCI like e2e4). Type 'resign' to stop.")
                else:
                    print("Your move (SAN like e4/Nf3 or UCI like e2e4). Type 'resign' to stop.")

                inp = input("> ").strip()

                if inp.lower() in {"resign", "quit", "exit"}:
                    game_end_reason = "resigned"
                    print("You resigned.")
                    break

                if inp.lower() == "y" and pending_player_suggestion is not None:
                    mv = pending_player_suggestion
                    followed_compass = True
                else:
                    mv = parse_user_move(board, inp)
                    if not mv:
                        print("Illegal/invalid. Try again.")
                        continue

                    # NEW: check if manually entered move matches suggestion
                    if pending_player_suggestion is not None and mv == pending_player_suggestion:
                        followed_compass = True
                    else:
                        followed_compass = False

                 # eval after human move

                board_before = board.copy(stack=False)
                eval_before = eval_white_cp(board_before, think_ms=80)

                # store suggestion BEFORE clearing
                suggested_move = pending_player_suggestion

                san = board.san(mv)
                board.push(mv)
                clear_compass()
                pending_player_suggestion = None

                eval_after = eval_white_cp(board, think_ms=80)

                node = node.add_variation(mv)
                print(f"You played: {san}\n")

                reading_one = generate_reading_for_move(
                    board_before,
                    mv,
                    eval_before,
                    eval_after,
                    tone_mod,
                )

                move_label = classify_notable_move(board_before, mv, reading_one)

                event_class = classify_move_event(followed_compass, move_label)

                player_event_log.append({
                    "event_class": event_class,          # a / b / c / d
                    "followed_compass": followed_compass,
                    "move_label": move_label,            # brilliant / great / miss / blunder / None
                    "san": san,
                    "uci": mv.uci(),
                    "reading": reading_one,
                })

                event_class = classify_move_event(followed_compass, move_label)
                print_this_reading = (event_class != "b")

                print("=== POSITION SIGNAL ===")
                print(f"This move {reading_one['move_feedback']['position_change_text']}.")
                print(f"Win probability now: {reading_one['move_feedback']['win_probability_percent']}%")
                if move_label is not None:
                    print(f"Move label: {move_label}")
                print()

                send_oled_status(
                    board,
                    reading_one["move_feedback"]["position_change_text"],
                    reading_one["move_feedback"]["eval_after_cp"],
                    reading_one["move_feedback"]["win_probability_percent"],
                )

                if print_this_reading:
                    board_text = render_board_ascii(board)
                    reading_text = render_reading_text_event(reading_one)
                    suggested_str = board_before.san(suggested_move) if suggested_move else "-"
                    played_str = san

                    event_text = (
                        f"Suggested: {suggested_str}\n"
                        f"Played:    {played_str}\n\n"
                        f"{board_text}\n\n"
                        f"{reading_text}"
                    )

                    print("=== EVENT READING ===")
                    print(event_text)
                    print()
                    send_to_printer(event_text)

                # stop immediately if human move ended the game
                if board.is_checkmate():
                    print("Checkmate.")
                    print("Game over:", board.result())
                    break
                elif board.is_game_over():
                    print("Game over:", board.result())
                    break

            else:
                # BOT TURN
                board_before = board.copy(stack=False)
                eval_before = eval_white_cp(board_before, think_ms=80)

                mv = you_move(board)
                san = board.san(mv)
                board.push(mv)

                eval_after = eval_white_cp(board, think_ms=80)

                node = node.add_variation(mv)
                print(f"mebot plays: {san}  ({mv.uci()})\n")

                # show bot move on big board
                send_move_to_led(mv)

                # ask human to physically enact bot move
                moved_for_bot = input("Did player move the bot piece based on indication? (y/n): ").strip().lower()

                if moved_for_bot == "y":
                    player_color = chess.WHITE if you_are_white else chess.BLACK
                    bot_color = not player_color

                    danger = compute_danger_field(board, player_color, bot_color)

                    print("=== DANGER FIELD ===")
                    print("Check to player:", danger["check_to_player"])
                    print("Mate to player:", danger["mate_to_player"])
                    print("Vulnerable player pieces:", danger["vulnerable_piece_squares"])
                    print("Attack line squares:", danger["attack_line_squares"])
                    print("Combined:", danger["combined"])
                    print()

                    if danger["mate_to_player"]:
                        send_mate_to_led()
                        send_oled_status(board, "worsened your position", -100000, 0.0)
                        print("Checkmate.")
                        print("Game over:", board.result())
                        break

                    elif danger["check_to_player"]:
                        send_check_to_led()
                        send_oled_status(board, "worsened your position", eval_after, 0.0)

                    else:
                        send_field_to_led(danger["combined"])

                    # compass stays exactly as it is
                                        # store compass suggestion for the upcoming human turn
                    if not board.is_game_over():
                        pending_player_suggestion = suggest_player_move_to_compass(board)
                    else:
                        pending_player_suggestion = None

                else:
                    pending_player_suggestion = None
                    print("Skipping danger/check/mate state until bot move is physically enacted.\n")

                if board.is_game_over():
                    print("Game over:", board.result())
                    break

        # this is OUTSIDE the loop
        if board.is_game_over():
            print("Final position:")
            print(board)
            print("Game over:", board.result())
        else:
            print("Game stopped.")

    finally:
        out = Path("/Users/janelu/Documents/MDP-Thesis-2025/chess-emotion/data/processed/last_mebot_game.pgn")
        with open(out, "w") as f:
            print(game, file=f)
        print(f"Saved PGN → {out}")
        summary_text = render_final_summary_slip(game, board, game_end_reason, player_event_log)
        print("=== FINAL SUMMARY SLIP ===")
        print(summary_text)
        print()
        send_to_printer(summary_text)

        try:
            engine.quit()
        except:
            pass

        if led_ser:
            try:
                led_ser.close()
            except:
                pass


# ---------- Mode 2 unchanged ----------
def demo_from_pgn(pgn_path: Path):
    ...
def demo_all_pgns():
    ...


if __name__ == "__main__":
    play_interactive()

if compass_ser:
    try:
        compass_ser.close()
    except:
        pass