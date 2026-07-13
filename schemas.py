"""Schemas Pydantic das respostas da API.

Separa o CONTRATO HTTP (o que o front-end recebe) da logica de catalogo/scraping.
Usados como `response_model` nas rotas -> FastAPI valida o shape, dropa campos
nao declarados e documenta tudo no OpenAPI. Evita o bug de "payload incompleto"
(campos somem em silencio) acontecer de novo.

Dois itens distintos:
  - MangaHomeItem  : card da home (enxuto, capa local + metadados essenciais).
  - MangaSearchItem: resultado de busca (mais rico: sinopse multi-idioma,
                     titulos alternativos, status, idioma, etc.).

As respostas vem em ENVELOPE (items + sections + total + flags), nao lista pura,
porque o front depende de `sections` (carrossel "Em alta" + secoes do catalogo),
`total` (contador) e `refreshing` (polling enquanto o catalogo aquece).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class MangaHomeItem(BaseModel):
    """Item do card da home — so o que MangaCard.jsx renderiza."""

    id: str
    title: str
    cover_path: str = ""
    cover_url: str = ""
    cover_fallbacks: list[str] = Field(default_factory=list)
    source: str = ""
    description: str = ""
    genres: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    rating: float | None = None
    chapter_count: int | None = None
    chapter_preview: list[str] = Field(default_factory=list)
    chapter_status: str = "pending"
    latest_chapter: str = ""
    updated_at: str = ""
    source_url: str = ""
    chapter_languages: list[str] = Field(default_factory=list)


class MangaSearchItem(BaseModel):
    """Resultado de busca — superset do item da home com metadados ricos."""

    id: str
    title: str
    slug: str = ""
    source: str = ""
    provider: str = ""
    section: str = ""
    cover_path: str = ""
    cover_url: str = ""
    cover_fallbacks: list[str] = Field(default_factory=list)
    description: str = ""
    descriptions_map: dict[str, str] = Field(default_factory=dict)
    genres: list[str] = Field(default_factory=list)
    authors: list[str] = Field(default_factory=list)
    alternative_titles: list[str] = Field(default_factory=list)
    chapter_languages: list[str] = Field(default_factory=list)
    rating: float | None = None
    chapter_count: int | None = None
    latest_chapter: str = ""
    updated_at: str = ""
    status: str = ""
    language: str = ""
    source_url: str = ""


class HomeSection(BaseModel):
    """Secao da home (ex.: 'Em alta' carrossel, 'Recem-lancados - MangaDex')."""

    title: str = ""
    layout: str = ""
    items: list[MangaHomeItem] = Field(default_factory=list)


class SearchSection(BaseModel):
    """Secao de resultados de busca."""

    title: str = ""
    layout: str = ""
    items: list[MangaSearchItem] = Field(default_factory=list)


class HomeResponse(BaseModel):
    """Envelope da home: itens tipados + secoes + metadados de paginacao/estado."""

    items: list[MangaHomeItem] = Field(default_factory=list)
    sections: list[HomeSection] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    sources: list[str] = Field(default_factory=list)
    cached: bool = False
    refreshing: bool = False


class SearchResponse(BaseModel):
    """Envelope da busca: itens ricos + secoes + metadados."""

    items: list[MangaSearchItem] = Field(default_factory=list)
    sections: list[SearchSection] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    sources: list[str] = Field(default_factory=list)
    cached: bool = False
    refreshing: bool = False
