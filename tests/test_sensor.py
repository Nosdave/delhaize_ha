"""Standalone tests for Delhaize sensor value helpers."""

from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys
import types


def _module(name: str) -> types.ModuleType:
    """Create and register a module stub."""
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def _load_sensor() -> types.ModuleType:
    """Load sensor.py without requiring a Home Assistant installation."""
    root = Path(__file__).resolve().parents[1]
    custom_components_path = root / "custom_components"
    integration_path = custom_components_path / "delhaize_ha"

    custom_components = sys.modules.setdefault(
        "custom_components", types.ModuleType("custom_components")
    )
    custom_components.__path__ = [str(custom_components_path)]
    package = types.ModuleType("custom_components.delhaize_ha")
    package.__path__ = [str(integration_path)]
    sys.modules["custom_components.delhaize_ha"] = package

    homeassistant = _module("homeassistant")
    homeassistant.__path__ = []
    components = _module("homeassistant.components")
    components.__path__ = []
    sensor_stub = _module("homeassistant.components.sensor")

    class SensorEntity:
        """Sensor entity stub."""

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        """Sensor description stub."""

        key: str
        name: str | None = None
        icon: str | None = None

    sensor_stub.SensorEntity = SensorEntity
    sensor_stub.SensorEntityDescription = SensorEntityDescription

    config_entries = _module("homeassistant.config_entries")
    config_entries.ConfigEntry = object
    constants = _module("homeassistant.const")
    constants.CONF_USERNAME = "username"
    core = _module("homeassistant.core")
    core.HomeAssistant = object

    helpers = _module("homeassistant.helpers")
    helpers.__path__ = []
    entity = _module("homeassistant.helpers.entity")
    entity.DeviceInfo = dict
    entity_platform = _module("homeassistant.helpers.entity_platform")
    entity_platform.AddEntitiesCallback = object
    update_coordinator = _module("homeassistant.helpers.update_coordinator")

    class CoordinatorEntity:
        """Generic coordinator entity stub."""

        @classmethod
        def __class_getitem__(cls, item: object) -> type[CoordinatorEntity]:
            return cls

    update_coordinator.CoordinatorEntity = CoordinatorEntity
    util = _module("homeassistant.util")
    util.slugify = lambda value: value.lower().replace(" ", "_")

    coordinator = _module("custom_components.delhaize_ha.coordinator")
    coordinator.DelhaizeDataUpdateCoordinator = object

    spec = importlib.util.spec_from_file_location(
        "custom_components.delhaize_ha.sensor",
        integration_path / "sensor.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sensor = _load_sensor()


def test_product_images_and_links_are_exposed_in_both_product_lists() -> None:
    """Dashboard rows receive absolute product and preferred thumbnail URLs."""
    product = {
        "name": "Product",
        "url": "/fr/shop/p/123",
        "images": [
            {"format": "zoom", "url": "/medias/large.jpg"},
            {"format": "respThumbnail", "url": "/medias/thumbnail.jpg"},
        ],
    }
    offer = {"id": "offer-1", "products": [product]}
    personal = sensor._personal_offer_product_discount_list_for_offer(offer)[0]
    burnable = sensor._burnable_offer_discount_product_list(
        {"burnable_offers": {"burnableOfferList": [offer]}}
    )[0]
    for row in (personal, burnable):
        assert row["product_url"] == "https://www.delhaize.be/fr/shop/p/123"
        assert row["product_image_url"] == (
            "https://static.delhaize.be/medias/thumbnail.jpg"
        )


def test_product_links_handle_missing_images_and_absolute_urls() -> None:
    """Missing or malformed image entries are skipped without breaking rows."""
    assert sensor._product_links({}) == {}
    assert sensor._product_links({"images": "invalid"}) == {}
    assert sensor._product_links({"images": [None, {}, {"url": ""}]}) == {}
    assert sensor._product_links(
        {
            "url": "https://www.delhaize.be/nl/shop/p/123",
            "images": [None, {}, {"url": "https://static.delhaize.be/image.jpg"}],
        }
    ) == {
        "product_url": "https://www.delhaize.be/nl/shop/p/123",
        "product_image_url": "https://static.delhaize.be/image.jpg",
    }


def test_personal_offer_products_use_points_value_over_original_price() -> None:
    """Personal point percentage should use wasPrice and one cent per point."""
    data = {
        "personal_offers": {
            "personalOfferList": [
                {
                    "id": "offer-1",
                    "name": "75 bonus points",
                    "active": False,
                    "points": 75,
                    "offerRedeemed": False,
                    "products": [
                        {
                            "code": "123",
                            "name": "Test product",
                            "price": {
                                "wasPrice": "€ 3,00",
                                "formattedValue": "€ 2,50",
                                "value": 2.5,
                            },
                        }
                    ],
                }
            ]
        }
    }

    assert sensor._personal_offer_product_discount_list(data) == [
        {
            "offer": "75 bonus points",
            "active": False,
            "product": "Test product",
            "product_code": "123",
            "points": 75,
            "points_value": 0.75,
            "required_quantity": 1,
            "qualifying_price_value": 3.0,
            "original_price": "€ 3,00",
            "original_price_value": 3.0,
            "discount_percentage": 25.0,
            "discount_percentage_formatted": "25%",
        }
    ]


def test_personal_offer_products_fall_back_to_current_price() -> None:
    """The normal product value should be used when wasPrice is absent."""
    offer = {
        "name": "100 bonus points",
        "points": "100",
        "products": [
            {
                "name": "Second product",
                "price": {"formattedValue": "€ 4,00", "value": 4},
            }
        ],
    }

    assert sensor._personal_offer_product_discount_list_for_offer(offer)[0] == {
        "offer": "100 bonus points",
        "product": "Second product",
        "points": 100,
        "points_value": 1.0,
        "required_quantity": 1,
        "qualifying_price_value": 4.0,
        "original_price": "€ 4,00",
        "original_price_value": 4.0,
        "discount_percentage": 25.0,
        "discount_percentage_formatted": "25%",
    }


def test_personal_offer_percentage_uses_required_item_quantity() -> None:
    """Points awarded for two items should be divided by both items' price."""
    offer = {
        "name": "100 punten voor 2",
        "points": 100,
        "products": [
            {
                "code": "multi-1",
                "name": "Multi-buy product",
                "price": {"formattedValue": "€ 4,00", "value": 4},
            }
        ],
    }

    assert sensor._personal_offer_product_discount_list_for_offer(offer)[0] == {
        "offer": "100 punten voor 2",
        "product": "Multi-buy product",
        "product_code": "multi-1",
        "points": 100,
        "points_value": 1.0,
        "required_quantity": 2,
        "qualifying_price_value": 8.0,
        "original_price": "€ 4,00",
        "original_price_value": 4.0,
        "discount_percentage": 12.5,
        "discount_percentage_formatted": "12.5%",
    }


def test_personal_offer_quantity_can_span_name_and_promotion() -> None:
    """The points and quantity text may be split and reversed across fields."""
    offer = {
        "name": "Bij aankoop van minstens 3 producten",
        "promotion": "150 punten",
        "points": 150,
        "products": [{"price": {"value": 2}}],
    }

    product = sensor._personal_offer_product_discount_list_for_offer(offer)[0]

    assert product["required_quantity"] == 3
    assert product["qualifying_price_value"] == 6.0
    assert product["discount_percentage"] == 25.0


def test_product_row_exposes_quantity_from_separate_promotion_text() -> None:
    """Product rows should retain a multi-buy promotion stored outside its name."""
    offer = {
        "name": "Personal offer 115",
        "promotion": "115 punten voor 2",
        "points": 115,
        "products": [{"name": "Product", "price": {"value": 4}}],
    }

    product = sensor._personal_offer_product_discount_list_for_offer(offer)[0]

    assert product["offer"] == "Personal offer 115"
    assert product["promotion"] == "115 punten voor 2"
    assert product["required_quantity"] == 2
    assert product["qualifying_price_value"] == 8.0
    assert product["discount_percentage"] == 14.4


def test_quantity_parser_normalizes_cms_markup_and_spacing() -> None:
    """Rendered promotion text may contain HTML and non-breaking spaces."""
    offer = {
        "name": "Personal offer 115",
        "promotion": "<strong>115 punten</strong><br>voor&nbsp;2",
        "points": 115,
        "products": [{"price": {"value": 4}}],
    }

    product = sensor._personal_offer_product_discount_list_for_offer(offer)[0]

    assert product["required_quantity"] == 2
    assert product["qualifying_price_value"] == 8.0
    assert product["discount_percentage"] == 14.4


def test_quantity_parser_normalizes_api_underscore_separator() -> None:
    """PersonalOffersV2 separates promotion words with underscores."""
    offer = {
        "name": "Delhaize rauwe of gerookte ham",
        "promotion": "115 punten_voor 2",
        "points": 115,
        "products": [{"price": {"value": 2.05}}],
    }

    product = sensor._personal_offer_product_discount_list_for_offer(offer)[0]

    assert product["required_quantity"] == 2
    assert product["qualifying_price_value"] == 4.1
    assert product["discount_percentage"] == 28.0



def test_burnable_offer_uses_points_as_purchase_price() -> None:
    """Savings subtract the redeemed points value from the normal price."""
    data = {
        "burnable_offers": {
            "burnableOfferList": [
                {
                    "id": "chipito-offer",
                    "name": "Cheetos Chipito chips met kaassmaak 125g",
                    "priceToBurn": 125,
                    "daysRemaining": 14,
                    "products": [
                        {
                            "name": "Chips Chipito Fromage",
                            "price": {
                                "formattedValue": "€ 1,79",
                                "value": 1.79,
                            },
                        }
                    ],
                }
            ]
        }
    }

    assert sensor._burnable_offer_discount_product_list(data) == [
        {
            "offer": "Cheetos Chipito chips met kaassmaak 125g",
            "discount_points": 125,
            "point_price_value": 1.25,
            "original_price_value": 1.79,
            "discount_percentage": 30.2,
            "discount_percentage_formatted": "30.2%",
            "days_remaining": 14,
            "product": "Chips Chipito Fromage",
            "original_price": "€ 1,79",
        }
    ]


def test_burnable_range_savings_use_original_price_and_handle_missing_costs() -> None:
    """Range offers use wasPrice and omit savings when prices are unavailable."""
    for points, price, expected in (
        (125, {"wasPrice": "4.00", "value": 3}, 68.8),
        (0, {"value": 4}, 100.0),
        (400, {"value": 4}, 0.0),
        (None, {"value": 4}, None),
        (125, {}, None),
        (125, {"value": 0}, None),
    ):
        data = {
            "burnable_offer_ranges": [
                {
                    "id": "range-1",
                    "priceToBurn": points,
                    "products": [{"price": price}],
                }
            ]
        }
        row = sensor._burnable_offer_discount_product_list(data)[0]
        assert row.get("discount_percentage") == expected
