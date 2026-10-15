"""Presentation names and pagination checked with synthetic rows and real SQL."""

import pytest
from api.db.models import Item
from api.routers.items import _item_summary
from api.services.items import (
    CatalogOrderBookData,
    ItemWithLatestSnapshot,
    _item_filters,
    _item_order_by,
)
from api.services.vehicle_display_names import DISPLAY_NAMES, vehicle_display_name
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def database():
    engine = create_engine("sqlite://")
    Item.__table__.create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Item(
                    id=index,
                    external_key=key,
                    name=key,
                    category="SYNTHETIC",
                    is_active=True,
                )
                for index, key in enumerate(DISPLAY_NAMES, 1)
            ]
        )
        session.add(
            Item(
                id=99,
                external_key="unmapped-key",
                name="Synthetic Unknown",
                category="SYNTHETIC",
                is_active=False,
            )
        )
        session.commit()
        yield session
    engine.dispose()


def query(database, *, search=None, offset=0, limit=100, order="asc", active=True):
    return list(
        database.scalars(
            select(Item)
            .where(
                *_item_filters(
                    search=search, category=None, rarity=None, is_active=active
                )
            )
            .order_by(*_item_order_by("name", order), Item.id.asc())
            .offset(offset)
            .limit(limit)
        )
    )


def test_verified_display_names_and_exact_fallback():
    assert vehicle_display_name("id50228_spz_12_3_lgs_germany", "raw") == "SPz 12-3 LGS"
    assert vehicle_display_name("M8", "raw") == "M8 LAC"
    assert vehicle_display_name("75mm Gun Motor Carriage M8", "raw") == "M8A1 GMC"
    assert vehicle_display_name(" M8", "Synthetic fallback") == "Synthetic fallback"
    assert (
        vehicle_display_name("unknown-key", "Synthetic Unknown") == "Synthetic Unknown"
    )
    assert all("\u00a0" not in value for value in DISPLAY_NAMES.values())


def test_search_new_name_and_original_key(database):
    for search in ["SPz 12-3 LGS", "spz 12-3", "id50228_spz_12_3_lgs_germany"]:
        rows = query(database, search=search)
        assert [r.external_key for r in rows] == ["id50228_spz_12_3_lgs_germany"]
    assert query(database, search="No synthetic match") == []


@pytest.mark.parametrize("order", ["asc", "desc"])
def test_display_sort_is_consistent_across_pages(database, order):
    all_rows = query(database, order=order)
    names = [vehicle_display_name(row.external_key, row.name) for row in all_rows]
    assert names == sorted(DISPLAY_NAMES.values(), reverse=order == "desc")
    pages = [
        r.id
        for offset in range(0, 55, 7)
        for r in query(database, offset=offset, limit=7, order=order)
    ]
    assert pages == [r.id for r in all_rows]


def test_public_name_does_not_mutate_stored_identity(database):
    item = query(database, search="SPz 12-3 LGS")[0]
    original = item.name
    response = _item_summary(
        ItemWithLatestSnapshot(
            item=item,
            latest_snapshot=None,
            current_order_book=CatalogOrderBookData(status="no_capture"),
        )
    )
    assert response.name == "SPz 12-3 LGS"
    assert response.external_key == original
    assert response.current_order_book is None
    assert item.name == original
    database.expire_all()
    assert database.get(Item, item.id).name == original


def test_existing_filters_and_unmapped_items_are_preserved(database):
    rows = query(database, search="Unknown", active=False)
    assert [r.name for r in rows] == ["Synthetic Unknown"]
    assert query(database, search="Unknown", active=True) == []
