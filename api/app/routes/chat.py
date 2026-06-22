from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse
from app.services import llm

router = APIRouter()

# Test/Postman only — production transport is WS /ws/chat (deprecated, removed Phase 5).

SYSTEM_PROMPT = (
    "You are a travel assistant. "
    "Reply in the user's language."
)


def _build_prompt(question: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"### Question\n{question}\n\n"
        f"### Answer\n"
    )


@router.post("/chat", response_model=ChatResponse, deprecated=True)
async def chat(req: ChatRequest):
    prompt = _build_prompt(req.message)
    answer = await llm.generate(prompt)
    return ChatResponse(answer=answer, sources=[])
