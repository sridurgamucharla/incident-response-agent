"""ShopLite database: a deliberately small pool so a connection leak exhausts it fast."""
import random
from datetime import datetime, timezone

from sqlmodel import Field, Session, SQLModel, create_engine, select

from agent.config import get_settings

POOL_SIZE = 5

engine = create_engine(
    get_settings().shoplite_database_url,
    pool_size=POOL_SIZE,
    max_overflow=0,
    pool_timeout=2,  # seconds a request waits for a free connection before failing
    pool_pre_ping=True,
)


class Product(SQLModel, table=True):
    __tablename__ = "products"
    id: int | None = Field(default=None, primary_key=True)
    sku: str = Field(unique=True)
    name: str
    price_cents: int
    stock: int


class Order(SQLModel, table=True):
    __tablename__ = "orders"
    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id")
    sku: str
    quantity: int
    total_cents: int
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


CATALOG = [
    ("SL-TSHIRT-01", "Classic Tee", 1999), ("SL-HOODIE-02", "Zip Hoodie", 4999),
    ("SL-MUG-03", "Enamel Mug", 1499), ("SL-CAP-04", "Dad Cap", 2499),
    ("SL-SOCKS-05", "Crew Socks (3-pack)", 1299), ("SL-TOTE-06", "Canvas Tote", 1799),
    ("SL-BOTTLE-07", "Steel Bottle", 2999), ("SL-STICKER-08", "Sticker Pack", 599),
    ("SL-JACKET-09", "Rain Jacket", 8999), ("SL-BEANIE-10", "Wool Beanie", 2199),
    ("SL-NOTEBK-11", "Dot Notebook", 1199), ("SL-PIN-12", "Enamel Pin", 799),
]


def init_db() -> None:
    """Create tables and seed the catalog + a few orders (so /export has rows)."""
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if session.exec(select(Product)).first():
            return
        products = [Product(sku=s, name=n, price_cents=p, stock=500) for s, n, p in CATALOG]
        session.add_all(products)
        session.commit()
        for _ in range(20):
            p = random.choice(products)
            q = random.randint(1, 3)
            session.add(Order(product_id=p.id, sku=p.sku, quantity=q, total_cents=p.price_cents * q))
        session.commit()
