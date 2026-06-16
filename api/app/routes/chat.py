from fastapi import APIRouter

from app.models.schemas import ChatRequest, ChatResponse, Source
from app.services import llm, vector_store

router = APIRouter()

SYSTEM_PROMPT = (
    "Sen bir seyahat asistanısın. Sana verilen bağlam bilgilerini kullanarak "
    "kullanıcının sorusunu yanıtla. Kullanıcının dilinde cevap ver. "
    "Cevabın bağlamdaki bilgilere dayansın. Bağlamda bilgi yoksa bunu belirt."
)


def _build_prompt(question: str, chunks: list[dict]) -> str:
    context = "\n\n".join(
        f"[{c['city_name']} / {c['section']}]\n{c['text']}" for c in chunks
    )
    return (
        f"{SYSTEM_PROMPT}\n\n"
        f"### Bağlam\n{context}\n\n"
        f"### Soru\n{question}\n\n"
        f"### Cevap\n"
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    print("[chat] chunks.start", flush=True)
    chunks = await vector_store.search(req.message, top_k=5)
    print("[chat] chunks.ends", flush=True)
    print("[chat] prompt.start", flush=True)
    prompt = _build_prompt(req.message, chunks)
    print("[chat] prompt.ends", prompt, flush=True)
    answer = await llm.generate(prompt)
    print("[chat] prompt.answer", answer, flush=True)

    sources = [
        Source(
            text=c["text"],
            city_name=c["city_name"],
            section=c["section"],
            score=c["score"],
        )
        for c in chunks
    ]

    return ChatResponse(answer=answer, sources=sources)
