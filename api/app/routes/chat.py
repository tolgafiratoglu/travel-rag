from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse
from app.services import llm

router = APIRouter()

SYSTEM_PROMPT = (
    "Sen bir seyahat asistanısın. "
    "Kullanıcının dilinde cevap ver."
)


def _build_prompt(question: str) -> str:
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"### Soru\n{question}\n\n"
        f"### Cevap\n"
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    prompt = _build_prompt(req.message)
    answer = await llm.generate(prompt)
    return ChatResponse(answer=answer, sources=[])
