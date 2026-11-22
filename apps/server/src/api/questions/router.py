"""Questions the agent asks with `ask_user_question`, and the user's answers.

A question belongs to a chat. While the agent waits for it, the chat shows it as needing
input; once answered, expired or cancelled, it is closed. Questions of another user's or a
deleted chat do not exist.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Query
from sqlalchemy import select

from src.agent import questions
from src.api.common import UNAVAILABLE, paginate, router
from src.api.dependencies import CurrentUserDep, OwnedChatDep, ServicesDep, SessionDep
from src.api.questions.schemas import (
    AnswerQuestionArgs,
    AnswerQuestionResult,
    ListQuestionsInput,
    ListQuestionsResult,
    Question,
    QuestionsSort,
    ReadQuestionResult,
)
from src.db import models as db
from src.errors import ConflictError, InvalidRequestError, NotFoundError

router = router("", "questions")

_SORT = {QuestionsSort.ID: db.Question.id, QuestionsSort.CREATED_AT: db.Question.created_at}


async def get_owned_question(
    question_id: UUID, principal: CurrentUserDep, session: SessionDep
) -> db.Question:
    """Return the question of one of the user's chats."""
    question = await session.scalar(
        select(db.Question)
        .join(db.Chat, db.Chat.id == db.Question.chat_id)
        .where(
            db.Question.id == question_id,
            db.Chat.created_by == principal.user_id,
            db.Chat.deleted_at.is_(None),
        )
    )
    if question is None:
        raise NotFoundError(f"question {question_id} not found")
    return question


OwnedQuestionDep = Annotated[db.Question, Depends(get_owned_question)]


@router.get("/chats/{chat_id}/questions")
async def list_questions(
    chat_id: UUID,
    _: OwnedChatDep,
    session: SessionDep,
    params: Annotated[ListQuestionsInput, Query()],
) -> ListQuestionsResult:
    """List the agent's questions in the chat, oldest first by default."""
    query = select(db.Question).where(db.Question.chat_id == chat_id)
    if params.status:
        query = query.where(db.Question.status.in_(params.status))
    rows, info = await paginate(
        session,
        query,
        params,
        sort=_SORT[params.sort],
        direction=params.direction,
        key=db.Question.id,
    )
    return ListQuestionsResult(data=[Question.model_validate(row) for row in rows], info=info)


@router.get("/questions/{question_id}")
async def read_question(question: OwnedQuestionDep) -> ReadQuestionResult:
    """Return a question of the agent with its answer."""
    return ReadQuestionResult(data=Question.model_validate(question))


@router.post("/questions/{question_id}/answer", responses={503: UNAVAILABLE})
async def answer_question(
    question_id: UUID,
    question: OwnedQuestionDep,
    body: AnswerQuestionArgs,
    services: ServicesDep,
) -> AnswerQuestionResult:
    """Answer every question of one `ask_user_question` call.

    An answer that arrives while the agent still waits is returned to it directly; a later one
    reaches it as a new message, which starts a turn. Repeating the same answer is safe; a
    different answer, or one after the question expired or was cancelled, returns 409. A 503
    means the answer is stored but the agent was not notified; repeat it.
    """
    try:
        answered, queued = await questions.answer(
            services.sessions,
            question.id,
            [item.model_dump(exclude_none=True) for item in body.data],
            services.settings.approval.ttl,
        )
    except LookupError:
        raise NotFoundError(f"question {question_id} not found") from None
    except questions.InvalidAnswerError as error:
        raise InvalidRequestError(str(error)) from None
    except questions.QuestionClosedError as error:
        raise ConflictError(str(error)) from None
    if queued:
        await services.commands.input_available(answered.chat_id)
    return AnswerQuestionResult(data=Question.model_validate(answered))
