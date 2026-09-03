"""答题层：题目模型、AI 作答、本地缓存。"""

from .base import AnswerResult, AnswerProvider, Option, Question, QuestionType, normalize
from .cache import AnswerCache
from .ai import build_provider

__all__ = [
    "AnswerResult",
    "AnswerProvider",
    "Option",
    "Question",
    "QuestionType",
    "normalize",
    "AnswerCache",
    "build_provider",
]
