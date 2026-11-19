# src/mebot.py
import json
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

from text_render import render_reading_text
from text_render import render_reading_triad

# --- config ---
ENGINE_PATH = "/Users/janelu/Documents/MDP-Thesis-2025/chess-emotion/engines/stockfish/stockfish"
BOOK_CSV    = "/Users/janelu/Documents/MDP-Thesis-2025/chess-emotion/data/processed/you_book_moves.csv"
RAW_PGN_DIR = Path("/Users/janelu/Documents/MDP-Thesis-2025/chess-emotion/data/raw_pgn")
YOUR_HANDLES = {"JadedPineapple7", "SashaLyn256"}  # used in PGN demo to detect your turns

# NEW: LED link (set to your actual port; leave None to disable)
LED_SERIAL_PORT = "/dev/cu.usbserial-022F27BC"   # e.g., "/dev/tty.usbserial-1420"
LED_BAUD        = 115200                      # match your ESP32 sketch
USE_LED         = True                        # flip to False to disable quickly

# --- load your You-Book (FEN -> [(uci, prob), ...]) ---
book_df = pd.read_csv(BOOK_CSV)
book = {fen: list(zip(g["uci"].tolist(), g["prob"].tolist()))
        for fen, g in book_df.groupby("fen_before")}

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

def send_to_led(uci_move: str):
    """
    Send 4-byte coordinate (from,to) to the ESP32.
    We trim promotion letter if present (e.g., e7e8q -> e7e8).
    """
    if led_ser is None:
        return
    try:
        # ensure exactly 4 bytes: file rank file rank
        payload = uci_move[:4].encode("ascii")
        led_ser.write(payload)
        led_ser.flush()
        # tiny pacing so back-to-back moves don’t collapse
        time.sleep(0.01)
    except Exception as e:
        print(f"[LED] send error: {e}")

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

def play_interactive():
    print("=== mebot interactive ===")
    color = input("Play as (w/b)? ").strip().lower()
    assert color in {"w","b"}, "Type w or b"
    you_are_white = (color == "w")

    board = chess.Board()
    game = chess.pgn.Game()
    node = game

    tone_mod = load_tone_modifier("tone_modifier.json")

    # triad state (LOCAL)
    your_reading_buffer = []   # last 3 readings for YOUR moves
    your_move_count = 0

    try:
        while not board.is_game_over():
            is_human_turn = (board.turn and you_are_white) or ((not board.turn) and (not you_are_white))

            if is_human_turn:
                print(board)
                print("Your move (SAN like e4/Nf3 or UCI like e2e4). Type 'resign' to stop.")
                inp = input("> ").strip()
                if inp.lower() in {"resign","quit","exit"}:
                    print("You resigned.")
                    break

                mv = parse_user_move(board, inp)
                if not mv:
                    print("Illegal/invalid. Try again.")
                    continue

                # eval before
                board_before = board.copy(stack=False)
                eval_before = eval_white_cp(board_before, think_ms=80)

                san = board.san(mv)
                board.push(mv)

                # eval after
                eval_after = eval_white_cp(board, think_ms=80)

                node = node.add_variation(mv)
                print(f"You played: {san}\n")

                # build + store reading for THIS human move
                your_move_count += 1
                reading_one = generate_reading_for_move(board_before, mv, eval_before, eval_after, tone_mod)
                your_reading_buffer.append(reading_one)

                # keep last 3
                if len(your_reading_buffer) > 3:
                    your_reading_buffer = your_reading_buffer[-3:]

                # print triad after every 3 of YOUR moves
                if len(your_reading_buffer) == 3 and (your_move_count % 3 == 0):
                    print("=== READING TRIAD (Past / Present / Future) ===")
                    print(render_reading_triad(your_reading_buffer))
                    print()

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
                send_to_led(mv.uci())

        print("Game over:", board.result() if board.is_game_over() else "stopped")

    finally:
        out = Path("/Users/janelu/Documents/MDP-Thesis-2025/chess-emotion/data/processed/last_mebot_game.pgn")
        with open(out, "w") as f:
            print(game, file=f)
        print(f"Saved PGN → {out}")
        engine.quit()
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
