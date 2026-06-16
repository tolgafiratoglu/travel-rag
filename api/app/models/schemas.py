from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class Source(BaseModel):
    text: str
    city_name: str
    section: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: list[Source]
