"""Contract tests for the ordered, geometry-independent PyRomb CATG writer."""

from decimal import Decimal

import pytest

from pyromb.models.rorb import OrderedControl, OrderedNode, OrderedReach, render_ordered_catg


def _modern() -> tuple[str, tuple[OrderedControl, ...], tuple[OrderedNode, ...], tuple[OrderedReach, ...]]:
    controls = (
        OrderedControl(1, Decimal("5.000")),
        OrderedControl(3),
        OrderedControl(1, Decimal("4.000")),
        OrderedControl(4),
        OrderedControl(5, Decimal("2.500")),
        OrderedControl(7, physical_print="Outlet"),
    )
    nodes = (
        OrderedNode(1, "A", 0, 0, 2, True, False, Decimal("1.25000"), Decimal("0.2")),
        OrderedNode(2, "Join", 5000, 0, 4, False, False),
        OrderedNode(3, "B", 5000, 4000, 2, True, False, Decimal("2.50000"), Decimal("0.3")),
        OrderedNode(4, "Outlet", 7500, 0, 0, False, True),
    )
    reaches = (
        OrderedReach(1, "R1", 1, 2, 2500, 0, Decimal("5.000")),
        OrderedReach(2, "R2", 3, 2, 5000, 2000, Decimal("4.000")),
        OrderedReach(3, "R3", 2, 4, 6250, 0, Decimal("2.500")),
    )
    return "MODEL", controls, nodes, reaches


def test_order_and_physical_lengths_are_not_derived_from_normalized_xy() -> None:
    title, controls, nodes, reaches = _modern()
    data = render_ordered_catg(title, controls, (Decimal("1.25"), Decimal("2.5")),
                               (Decimal("0.2"), Decimal("0.3")), explicit_reach_types=True,
                               nodes=nodes, reaches=reaches)
    assert data.startswith("MODEL\nC RORB_GE 6.45\n")
    assert data.index("1,1,5.000,-99") < data.index("3\n") < data.index("1,1,4.000,-99")
    assert "5,1,2.500,-99" in data
    assert "7\nOutlet\n0\n" in data
    assert "C #NODES\nC      4\n" in data
    assert "C #REACHES\nC      3\n" in data
    assert "1.250000" in data
    assert "0.300000" in data


def test_legacy_no_drawing_and_print_annotations() -> None:
    result = render_ordered_catg(
        "Legacy", (OrderedControl(1, Decimal("1.000"), "Source Sub-area A"),
                   OrderedControl(7, label="Note", physical_print="Actual Outlet")),
        (Decimal("2.123456789"),), (Decimal(0),), explicit_reach_types=False,
    )
    assert "C RORB_GE 002\nC #NODES\nC 0" in result
    assert "1, 1.000, -99, Source Sub-area A" in result
    assert "7, Note\nActual Outlet\n0\n" in result
    assert "2.123456789" in result


@pytest.mark.parametrize(("which", "message"), [
    ("length", "3-decimal"),
    ("label", "independent code-1 label"),
    ("missing_drawing", "requires explicit ordered graphical"),
    ("area", "6-decimal"),
])
def test_unsupported_values_are_rejected(which: str, message: str) -> None:
    title, controls, nodes, reaches = _modern()
    areas = (Decimal("1.25"), Decimal("2.5"))
    if which == "length":
        controls = (OrderedControl(1, Decimal("5.0001")), *controls[1:])
    elif which == "label":
        controls = (OrderedControl(1, Decimal(5), label="not serializable"), *controls[1:])
    elif which == "missing_drawing":
        nodes, reaches = (), ()
    else:
        areas = (Decimal("1.2500001"), Decimal("2.5"))
        nodes = (OrderedNode(1, "A", 0, 0, 2, True, False, Decimal("1.2500001"), Decimal("0.2")),
                 *nodes[1:])
    with pytest.raises(ValueError, match=message):
        render_ordered_catg(title, controls, areas, (Decimal("0.2"), Decimal("0.3")),
                            explicit_reach_types=True, nodes=nodes, reaches=reaches)


def test_legacy_intermediate_and_terminal_prints_preserve_order() -> None:
    records = (
        OrderedControl(1, Decimal("1.0"), label="Reach Sub-area A"),
        OrderedControl(7, physical_print="Interior"),
        OrderedControl(5, Decimal("2.5"), label="Downstream"),
        OrderedControl(7, physical_print="Outlet"),
    )
    data = render_ordered_catg(
        "Two print sites", records, (Decimal("1.25"),), (Decimal(0),),
        explicit_reach_types=False,
    )
    assert "1, 1.0, -99, Reach Sub-area A\n7\nInterior\n5, 2.5, -99, Downstream\n7\nOutlet\n0\n" in data

