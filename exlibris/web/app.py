from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select

from exlibris.config import Settings
from exlibris.database import get_engine, init_db
from exlibris.models import Book

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"


def create_app(settings: Settings) -> FastAPI:
    engine = get_engine(settings.database_path)
    SessionLocal = init_db(engine)

    app = FastAPI(title="ExLibris", version="0.1.0")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", response_class=HTMLResponse)
    def library(
        request: Request,
        q: str | None = Query(default=None),
        sort: str = Query(default="title"),
    ) -> HTMLResponse:
        with SessionLocal() as session:
            query = select(Book)
            if q:
                pattern = f"%{q.strip()}%"
                query = query.where(
                    or_(
                        Book.title.ilike(pattern),
                        Book.authors.ilike(pattern),
                        Book.file_name.ilike(pattern),
                        Book.isbn.ilike(pattern),
                    )
                )

            sort_columns = {
                "title": Book.title.collate("NOCASE"),
                "author": Book.authors.collate("NOCASE"),
                "published": Book.published_date.desc().nulls_last(),
                "size": Book.file_size,
                "scanned": Book.last_scanned_at,
            }
            order_by = sort_columns.get(sort, Book.title.collate("NOCASE"))
            query = query.order_by(order_by, Book.id)

            books = session.scalars(query).all()
            total = session.scalar(select(func.count()).select_from(Book)) or 0

        return templates.TemplateResponse(
            request,
            "library.html",
            {
                "books": books,
                "total": total,
                "query": q or "",
                "sort": sort,
            },
        )

    @app.get("/book/{book_id}", response_class=HTMLResponse)
    def book_detail(request: Request, book_id: int) -> HTMLResponse:
        with SessionLocal() as session:
            book = session.get(Book, book_id)
            if book is None:
                return templates.TemplateResponse(
                    request,
                    "not_found.html",
                    {"message": "Book not found"},
                    status_code=404,
                )

        return templates.TemplateResponse(
            request,
            "book_detail.html",
            {"book": book},
        )

    return app
