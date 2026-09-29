"""ShopLite: a tiny store that DéjàVu watches, plus chaos endpoints that break it.

Run from the project root:  python -m uvicorn shoplite.app:app --port 8001 --no-access-log
"""
import time
import traceback
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlmodel import Session, desc, select

from agent.config import get_settings
from shoplite.chaos import FAULTS, chaos
from shoplite.db import Order, Product, engine, init_db
from shoplite.logs import get_logger
from shoplite.payments import PaymentAuthError, PaymentGatewayTimeout, charge

LOG_FILE = get_settings().log_path / "shoplite.log"
log = get_logger(LOG_FILE)

# Each store route is its own "service", so DéjàVu keeps one runbook per service.
ROUTE_SERVICE = {"/products": "catalog-api", "/checkout": "checkout-api", "/export": "export-api"}

STATUS_FOR = {
    PaymentGatewayTimeout: 504,
    PaymentAuthError: 502,
    PoolTimeoutError: 503,
    MemoryError: 503,
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield
    chaos.reset()


app = FastAPI(title="ShopLite", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def json_request_log(request: Request, call_next):
    service = ROUTE_SERVICE.get(request.url.path)
    if service is None:  # don't log chaos/health/docs traffic
        return await call_next(request)

    start = time.perf_counter()
    error, exc_type, stack = None, None, None
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception as exc:  # the real failure, logged with its real traceback
        status = next((code for cls, code in STATUS_FOR.items() if isinstance(exc, cls)), 500)
        exc_type = type(exc).__name__
        message = str(exc).splitlines()[0] if str(exc) else ""
        error = f"{exc_type}: {message}"[:300]
        stack = app_stack(exc)
        response = JSONResponse({"detail": error}, status_code=status)

    fields = {
        "service": service,
        "route": request.url.path,
        "method": request.method,
        "status": status,
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
        "error": error,
        "exception_type": exc_type,
        "version": chaos.version,
        "db_pool_in_use": engine.pool.checkedout(),
        "trace_id": uuid.uuid4().hex[:16],
    }
    if stack:
        fields["stack"] = stack
    level = 40 if status >= 500 else 30 if status >= 400 else 20
    log.log(level, error or "ok", extra={"fields": fields})
    return response


def app_stack(exc: Exception) -> str:
    """Traceback limited to our own code (framework/library frames are noise for triage)."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if ".venv" not in f.filename
              and "site-packages" not in f.filename] or traceback.extract_tb(exc.__traceback__)[-2:]
    lines = traceback.format_list(frames[-4:]) + traceback.format_exception_only(exc)
    return "".join(lines)[-1200:]


def apply_chaos() -> None:
    """Sync dependency, so FastAPI runs it in a worker thread (it may block)."""
    chaos.on_request()


# ---------------- store routes ----------------

@app.get("/products", dependencies=[Depends(apply_chaos)])
def list_products():
    with Session(engine) as session:
        return session.exec(select(Product).order_by(Product.id)).all()


class CheckoutRequest(BaseModel):
    product_id: int
    quantity: int = Field(default=1, ge=1, le=10)


def _load_product(product_id: int) -> Product:
    with Session(engine) as session:
        product = session.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "product not found")
    return product


def _save_order(product: Product, quantity: int) -> int:
    with Session(engine) as session:
        order = Order(product_id=product.id, sku=product.sku, quantity=quantity,
                      total_cents=product.price_cents * quantity)
        session.add(order)
        session.commit()
        return order.id


@app.post("/checkout", dependencies=[Depends(apply_chaos)])
async def checkout(body: CheckoutRequest):
    product = await run_in_threadpool(_load_product, body.product_id)
    total = product.price_cents * body.quantity
    charge_id = await charge(total)
    order_id = await run_in_threadpool(_save_order, product, body.quantity)
    return {"order_id": order_id, "charge_id": charge_id, "total_cents": total}


@app.get("/export", dependencies=[Depends(apply_chaos)])
def export_orders():
    with Session(engine) as session:
        rows = [o.model_dump() for o in session.exec(select(Order).order_by(desc(Order.id)).limit(100))]
    # v2.4.1 "renamed" sku -> product_sku in the exporter but not in the schema.
    sku_key = "product_sku" if chaos.is_on("bad_deploy") else "sku"
    lines = ["order_id,sku,quantity,total_cents,created_at"]
    lines += [f"{r['id']},{r[sku_key]},{r['quantity']},{r['total_cents']},{r['created_at'].isoformat()}"
              for r in rows]
    return PlainTextResponse("\n".join(lines), media_type="text/csv")


# ---------------- ops routes ----------------

@app.get("/health")
def health():
    return {"status": "ok", "version": chaos.version}


@app.get("/chaos")
def chaos_status():
    return chaos.status()


@app.post("/chaos/reset")
def chaos_reset():
    chaos.reset()
    return chaos.status()


@app.post("/chaos/{fault}")
def chaos_enable(fault: str):
    if fault not in FAULTS:
        raise HTTPException(404, f"unknown fault; choose from {list(FAULTS)}")
    chaos.enable(fault)
    return chaos.status()


@app.delete("/chaos/{fault}")
def chaos_disable(fault: str):
    if fault not in FAULTS:
        raise HTTPException(404, f"unknown fault; choose from {list(FAULTS)}")
    chaos.disable(fault)
    return chaos.status()
