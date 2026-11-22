"""Validation of native ask_user_question answers."""

import pytest
from pydantic import JsonValue

from src.agent.questions import InvalidAnswerError, late_answer_text, validate_answers

QUESTIONS: JsonValue = [
    {
        "id": "q1",
        "question": "Pick one",
        "options": [{"label": "A"}, {"label": "B"}],
        "multiSelect": False,
    },
    {
        "id": "q2",
        "question": "Pick many",
        "options": [{"label": "X"}, {"label": "Y"}],
        "multiSelect": True,
    },
]


def test_validate_answers_accepts_selected_labels_and_custom() -> None:
    answers = validate_answers(
        QUESTIONS,
        [
            {"id": "q1", "selected": ["A"]},
            {"id": "q2", "selected": ["X", "Y"]},
        ],
    )

    assert answers == [
        {"id": "q1", "selected": ["A"]},
        {"id": "q2", "selected": ["X", "Y"]},
    ]

    custom = validate_answers(
        QUESTIONS,
        [
            {"id": "q1", "selected": [], "custom": "other"},
            {"id": "q2", "selected": []},
        ],
    )
    assert custom == [
        {"id": "q1", "selected": [], "custom": "other"},
        {"id": "q2", "selected": []},
    ]


def test_validate_answers_rejects_unknown_ids_and_labels() -> None:
    with pytest.raises(InvalidAnswerError, match="q9"):
        validate_answers(QUESTIONS, [{"id": "q9", "selected": ["A"]}, {"id": "q2", "selected": []}])
    with pytest.raises(InvalidAnswerError, match="Z"):
        validate_answers(
            QUESTIONS, [{"id": "q1", "selected": ["A"]}, {"id": "q2", "selected": ["Z"]}]
        )
    with pytest.raises(InvalidAnswerError, match="one option"):
        validate_answers(
            QUESTIONS, [{"id": "q1", "selected": ["A", "B"]}, {"id": "q2", "selected": []}]
        )


def test_late_answer_text_is_the_native_payload() -> None:
    text = late_answer_text("call-1", QUESTIONS, [{"id": "q1", "selected": ["A"]}])

    assert '"kind":"answer_to_pending_question"' in text
    assert '"callId":"call-1"' in text
    assert '"tool":"ask_user_question"' in text
