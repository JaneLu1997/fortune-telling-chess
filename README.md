# Against the Oracle

**Fortune Telling × Computation**

*A computational divination system built through chess, the I Ching, and rule-based narrative generation.*

## About

Against the Oracle explores how systems of knowledge, prediction, and interpretation can be translated into computational rules.

By bringing together chess, an ancient divination system, and contemporary game-engine evaluation, the project transforms chess moves into fortune readings.

Rather than predicting the future, the system examines how meaning can be constructed through symbolic mappings, computational judgment, and generative narrative structures.

## How It Works

Every chess move carries several pieces of information: which piece moves, where it begins, where it lands, and how the move changes the position.

The system interprets these through three layers:

**01 — Archetypes**

Each chess piece is assigned a narrative identity:

- Pawn — Orphan
- Knight — Wanderer
- Bishop — Magician
- Rook — Warrior
- Queen — Ruler
- King — Altruist

**02 — I Ching / Symbolic Mapping**

The 64 squares of a chessboard are mapped to the 64 hexagrams of the I Ching.

A move from one square to another becomes a symbolic transition between two states, each carrying its own keywords and attributes.

**03 — Computational Evaluation**

Stockfish, an open-source chess engine, evaluates the changing chess position.

The evaluation informs the tone and intensity of the reading, introducing a computational judgment into the symbolic interpretation.

### Reading Grammar

These elements are combined through a rule-based narrative grammar written in Python.

**Chess Move → Symbolic Encoding → Computational Evaluation → Narrative Generation → Fortune Reading**

The resulting reading is generated through structured rules rather than a large language model.

## MeBot

The original thesis also includes MeBot, a chess opponent modeled partly on the artist's historical chess moves.

MeBot selects moves from a precomputed personal move database when matching positions are available, with Stockfish as a fallback.

## Repository Structure

```text
src/
  apparatus_v0405.py
  move_grammar.py
  text_render.py
  hexagrams.json
  piece_to_archetype.json
  flux_marks_iching_square_tables.json
  tone_modifier.json

data/
  processed/
    you_book_moves.csv
```

## Workshop

This repository is being adapted into a lightweight workshop exploring the relationship between fortune telling and computation.

Participants will examine how symbolic systems can be encoded as data, how computational evaluation can influence interpretation, and how structured rules can generate narratives.

**Status:** Work in progress.

The current main program is adapted from the original thesis installation and still contains hardware-specific configurations. A simplified workshop entry point and installation instructions will be added.

## Credits

Against the Oracle is an artistic research project developed as part of a graduate thesis at ArtCenter College of Design.

The project draws on the I Ching, chess, and Stockfish, the open-source chess engine.

## License

No license has been assigned to this repository at this stage.
