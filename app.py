"""Phase 1: deterministic keyword retrieval, no external model dependencies."""
import json
import unicodedata
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent
CORPUS = json.loads((ROOT / 'data/corpus.json').read_text())
app = FastAPI(title='知识问答检索评测台', version='0.1.0')


def normalize(text: str) -> str:
    return unicodedata.normalize('NFKC', text).casefold()


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=12, strict=True)

    @field_validator('query')
    @classmethod
    def strip_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError('query must not be blank')
        return value


class Fragment(BaseModel):
    id: str
    title: str
    content: str
    score: int
    matched_keywords: list[str]


class RetrievalResponse(BaseModel):
    query: str
    top_k: int
    results: list[Fragment]


@app.post('/retrieval', response_model=RetrievalResponse)
def retrieval(request: RetrievalRequest) -> RetrievalResponse:
    query = normalize(request.query)
    results = []
    for doc in CORPUS:
        matched = sorted({word for word in doc['keywords'] if normalize(word) in query})
        if matched:
            results.append(Fragment(id=doc['id'], title=doc['title'],
                                    content=doc['content'], score=len(matched),
                                    matched_keywords=matched))
    results.sort(key=lambda item: (-item.score, item.id))
    return RetrievalResponse(query=request.query, top_k=request.top_k,
                             results=results[:request.top_k])
