# =============================================================================
# text_chunker.py — Sentence-Boundary Text Chunker for TTS
# =============================================================================
#
# Buffers streaming text tokens from the LLM and emits chunks at natural
# linguistic boundaries (punctuation). This ensures ElevenLabs receives
# complete phrases/sentences for correct intonation and prosody.
#
# Usage:
#     chunker = TextChunker()
#     for token in llm_stream:
#         for chunk in chunker.feed(token):
#             await elevenlabs.send_text(chunk)
#     # At end of response, flush remaining text:
#     final = chunker.flush()
#     if final:
#         await elevenlabs.send_text(final)
# =============================================================================

from config import CHUNK_MIN_LENGTH, CHUNK_DELIMITERS


class TextChunker:
    """Buffers streaming text and emits chunks at sentence boundaries."""

    def __init__(
        self,
        min_length: int = CHUNK_MIN_LENGTH,
        delimiters: str = CHUNK_DELIMITERS,
    ):
        self._buffer: str = ""
        self._min_length = min_length
        self._delimiters = set(delimiters)

    def feed(self, token: str) -> list[str]:
        """
        Add a text token to the buffer. Returns a list of complete chunks
        (may be empty if no boundary was reached, or multiple if the token
        contained several sentence boundaries).
        """
        self._buffer += token
        chunks: list[str] = []

        # Scan for delimiter positions in the buffer
        while True:
            split_pos = self._find_boundary()
            if split_pos < 0:
                break

            candidate = self._buffer[: split_pos + 1].strip()
            self._buffer = self._buffer[split_pos + 1 :]

            if len(candidate) >= self._min_length:
                chunks.append(candidate)
            elif candidate:
                # Too short — prepend back to buffer for next round
                self._buffer = candidate + " " + self._buffer

        return chunks

    def flush(self) -> str | None:
        """
        Emit any remaining buffered text. Call this when the LLM signals
        end-of-turn to ensure no text is lost.
        """
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None

    def reset(self):
        """Clear the buffer entirely (e.g., on cancellation)."""
        self._buffer = ""

    def _find_boundary(self) -> int:
        """
        Find the rightmost delimiter position that leaves at least
        `min_length` characters in the chunk. Returns -1 if no valid
        boundary exists.
        """
        # Only look for boundaries if we have enough text
        if len(self._buffer) < self._min_length:
            return -1

        # Scan from the end backwards — prefer longer chunks
        # But only emit if we've accumulated enough
        best = -1
        for i, ch in enumerate(self._buffer):
            if ch in self._delimiters and (i + 1) >= self._min_length:
                best = i

        return best
