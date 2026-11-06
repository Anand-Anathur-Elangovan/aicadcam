"""Wake-name detection on meeting transcript text."""
import re
from dataclasses import dataclass


@dataclass
class NameMention:
    name_matched: str
    context_text: str
    question: str
    position: int


class NameDetector:
    def __init__(self, wake_name: str):
        self._wake_name = wake_name.strip().lower()
        parts = self._wake_name.split()
        self._patterns: list[re.Pattern] = []
        if len(parts) >= 2:
            self._patterns.append(
                re.compile(r"\b" + re.escape(" ".join(parts)) + r"\b", re.IGNORECASE)
            )
        for part in parts:
            if len(part) >= 3:
                self._patterns.append(
                    re.compile(r"\b" + re.escape(part) + r"\b", re.IGNORECASE)
                )

    @property
    def wake_name(self) -> str:
        return self._wake_name

    @wake_name.setter
    def wake_name(self, value: str) -> None:
        self.__init__(value)

    def check(self, text: str) -> NameMention | None:
        if not text or not self._patterns:
            return None
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                context = self._extract_context(text, match.start())
                question = self._extract_question(text, match.end())
                return NameMention(
                    name_matched=match.group(),
                    context_text=context,
                    question=question,
                    position=match.start(),
                )
        return None

    def _extract_context(self, text: str, mention_pos: int) -> str:
        sentences = re.split(r"(?<=[.!?])\s+", text)
        context_parts = []
        char_count = 0
        for sentence in sentences:
            start = char_count
            end = start + len(sentence)
            if start <= mention_pos <= end:
                idx = sentences.index(sentence)
                if idx > 0:
                    context_parts.append(sentences[idx - 1])
                context_parts.append(sentence)
                if idx < len(sentences) - 1:
                    context_parts.append(sentences[idx + 1])
                return " ".join(context_parts)
            char_count = end + 1
        return text[:300]

    def _extract_question(self, text: str, after_name_pos: int) -> str:
        remaining = text[after_name_pos:].strip()
        if not remaining:
            sentences = re.split(r"(?<=[.!?])\s+", text)
            for s in sentences:
                if "?" in s or any(
                    w in s.lower()
                    for w in ["what", "how", "when", "where", "why", "can", "could",
                              "would", "should", "do you", "think", "update", "status"]
                ):
                    return s.strip()
            return text[-200:].strip()

        sentences = re.split(r"(?<=[.!?])\s+", remaining)
        question_parts = []
        for s in sentences[:3]:
            question_parts.append(s)
            if "?" in s:
                break
        result = " ".join(question_parts).strip(" ,;:")
        return result if result else remaining[:200]
