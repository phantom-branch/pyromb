from __future__ import annotations

import json
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from .. import resources
from ..core.attributes.basin import Basin
from ..core.attributes.confluence import Confluence
from ..core.attributes.reach import ReachType
from ..core.traveller import Traveller
from ..core.model import Model



@dataclass(frozen=True)
class OrderedControl:
    """A native control, in the already-validated computation sequence."""

    code: int
    length_km: Decimal | None = None
    label: str = ""
    physical_print: str = ""


@dataclass(frozen=True)
class OrderedNode:
    """Graphical metadata only; XY coordinates are never physical reach lengths."""

    identifier: int
    name: str
    x: float
    y: float
    downstream: int
    source: bool
    outlet: bool
    area_km2: Decimal = Decimal(0)
    impervious_fraction: Decimal = Decimal(0)


@dataclass(frozen=True)
class OrderedReach:
    """A native graphical reach with an independently supplied physical length."""

    identifier: int
    name: str
    upstream: int
    downstream: int
    x: float
    y: float
    length_km: Decimal


def _ordered_number(value: Decimal, what: str, *, places: int | None = None) -> str:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{what} must be a finite nonnegative Decimal")
    if places is not None and value != value.quantize(Decimal(1).scaleb(-places)):
        raise ValueError(f"{what} cannot fit the RORB_GE graphical {places}-decimal field")
    return str(value)


def _ordered_label(value: str, what: str) -> str:
    if not isinstance(value, str) or value != value.strip() or any(c in value for c in (",", "\r", "\n", "\x00")):
        raise ValueError(f"{what} cannot contain commas, record delimiters or surrounding whitespace")
    return value


class VectorBlock():
    """Builds the vector block for the RORB control file.
    """

    def __init__(self) -> None:
        self._storedHydro: list[int] = []
        self._runningHydro: bool = False
        self._stateVector = []
        self._controlVector = []

        resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'resources')
        with open(os.path.join(resources_dir, 'formatting.json'), 'r') as f:
            self._formattingOptions = json.load(f)

    def step(self, traveller: Traveller) -> None:
        """ 
        Calculate action to take at the current step and store it in the VectorBlock's state.
        
        Step is to be used at every update of the Traveller. The RORB control vector 
        is then built from the VectorBlock's state after the catchment has been traversed.

        Parameters
        ----------
        traveller : Traveller
            The traveller for the catchment being built. 
        """
        self._state(traveller)
        self._control(self._stateVector[-1], traveller)

    def build(self, traveller: Traveller) -> str:
        """ 
        Builds the vector block string.
        
        Parameters
        ----------
        traveller: Traveller
            The traveller that traversed the catchment.
            
        Returns:
        -------
        str
            The vector block string to be used in the .catg file 
        """
        vectorStr = "0\n"                   # Start with code 0, reach types are specified in the control block.
        for s in self._controlVector:
            vectorStr += f"{s}\n"
        vectorStr += f"{self._subAreaStr(self._stateVector, traveller)}\n{self._fracImpStr(self._stateVector, traveller)}\n"
        return vectorStr


    def build_ordered(
        self,
        controls: Sequence[OrderedControl],
        areas_km2: Sequence[Decimal],
        impervious_fractions: Sequence[Decimal],
        *,
        explicit_reach_types: bool,
    ) -> str:
        """Render ordered records without reconstructing their order from drawing XY."""
        if not areas_km2 or len(areas_km2) != len(impervious_fractions):
            raise ValueError("One impervious fraction is required for each ordered source area")
        lines = ["0" if explicit_reach_types else "1"]
        sources = 0
        reaches = 0
        prints = 0
        for record in controls:
            code = record.code
            if type(code) is not int or code not in (1, 2, 3, 4, 5, 7):
                raise ValueError(f"Unsupported ordered RORB control code {code!r}")
            label = _ordered_label(record.label, f"Control {code} label")
            if code in (1, 2, 5):
                if record.length_km is None or record.length_km <= 0:
                    raise ValueError(f"Control {code} requires a positive physical length in kilometres")
                length = _ordered_number(
                    record.length_km, "Physical reach length", places=3 if explicit_reach_types else None
                )
                if explicit_reach_types:
                    if label:
                        raise ValueError(f"RORB_GE 6.45 cannot encode an independent code-{code} label")
                    lines.append(f"{code},1,{length},-99")
                else:
                    if code == 2:
                        raise ValueError("Code 2 needs RORB_GE 6.45 explicit-reach encoding")
                    lines.append(f"{code}, {length}, -99, {label}")
                reaches += 1
                if code in (1, 2):
                    sources += 1
            elif code in (3, 4):
                if record.length_km is not None or record.physical_print:
                    raise ValueError(f"Control {code} cannot carry reach or print operands")
                lines.append(f"{code}, {label}" if label else str(code))
            else:
                if record.length_km is not None:
                    raise ValueError("Control 7 cannot carry a reach")
                physical = _ordered_label(record.physical_print, "Physical print-location label")
                if physical == "0" or physical.startswith(("C ", "C\t")):
                    raise ValueError("The physical print-location record cannot be a zero terminator or comment")
                lines.extend((f"7, {label}" if label else "7", physical))
                prints += 1
        if sources != len(areas_km2) or not reaches or prints != 1 or controls[-1].code != 7:
            raise ValueError("Ordered control/source/print counts are inconsistent")
        lines.extend(("0", resources.rorb.AREA_TABLE_HEADER.rstrip("\n")))
        area_tokens = [*(_ordered_number(a, "Source area") for a in areas_km2), "-99"]
        lines.append(self._makeTable(area_tokens, "area_table"))
        lines.append(resources.rorb.FI_TABLE_HEADER.rstrip("\n"))
        for fraction in impervious_fractions:
            _ordered_number(fraction, "Impervious fraction")
            if fraction > 1:
                raise ValueError("Impervious fractions must be in [0, 1]")
        if explicit_reach_types:
            fraction_tokens = [*(str(f) for f in impervious_fractions), "-99"]
            lines.extend(("1", self._makeTable(fraction_tokens, "fi_table")))
        else:
            if any(f != 0 for f in impervious_fractions):
                raise ValueError("RORB_GE 002 cannot encode per-source impervious fractions")
            lines.append("0, -99")
        return "\n".join(lines) + "\n"

    def _state(self, traveller: Traveller) -> None:
        """Store the current state of the traveller within the catchment at each time step.

        This is necessary to know so that the correct control code can be returned.

        Control codes:
        - 1: If there is no running hydrograph and the node is a sub-area, set the running hydrograph.
        - 2: If there is a running hydrograph and the node is a sub-area and has no other visitable upstream reaches.
        - 3: If there is a running hydrograph and the node has other visitable upstream reaches, push node index onto stored hydrograph stack, set the running hydrograph to zero, and move to the most upstream point on that reach.
        - 4: If the node has a stored hydrograph and there is a running hydrograph, pop the stored hydrograph from the stack.
        - 5: If there is a running hydrograph and the node is not a sub-area and does not have upstream and there is nothing stored.

        Parameters
        ----------
        traveller : Traveller
            The traveller traversing this catchment.
        """
        i = traveller._pos
        up = traveller.top(i)

        if i == traveller._endSentinel:
            ret = (0, i)
        elif (self._runningHydro == False) and (isinstance(traveller._catchment._vertices[i], Basin)):
            self._runningHydro = True
            traveller.next()
            ret = (1, i)
        elif (self._storedHydro) and (self._storedHydro[-1] == i) and (self._runningHydro):
            self._storedHydro.pop()
            ret = (4, i)
        elif (self._runningHydro) and (isinstance(traveller._catchment._vertices[i], Basin)) and (up == i):
            traveller.next()
            ret = (2, i)
        elif (self._runningHydro) and (up != i):
            self._storedHydro.append(i)
            self._runningHydro = False
            traveller.next()
            ret = (3, i)
        elif (self._runningHydro) and (isinstance(traveller._catchment._vertices[i], Confluence)) and (up == i):
            traveller.next()
            ret = (5, i)

        self._stateVector.append(ret)

    def _control(self, code: tuple, traveller: Traveller) -> None:
        """Format a control vector string according to the RORB manual Table 5-1 p.52 (version 6).

        Parameters
        ----------
        code : tuple
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the traveller when the command code was created.

        traveller : Traveller
            The traveller traversing this catchment.
        """
        if code[0] in (1, 2, 5):
            try:
                r = traveller.getReach(code[1])
                if (r.type == ReachType.NATURAL) or (r.type == ReachType.DROWNED):
                    ret = f"{code[0]},{r.type.value},{r.length() / 1000:.3f},-99"
                else:
                    ret = f"{code[0]},{r.type.value},{r.length() / 1000:.3f},{r.getSlope()},-99"
            except:
                ret = f"{7}\n\n{0}"

        if (code[0] == 3) or (code[0] == 4):
            ret = f"{code[0]}"

        if (code[0] == 0):
            ret = f"{7}\n\n'{0}"

        self._controlVector.append(ret)

    def _subAreaStr(self, code: tuple, traveller: Traveller) -> str:
        """Format the subarea string according to the RORB manual.

        Parameters
        ----------
        code : tuple
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the traveller when the command code was created.

        traveller : Traveller
            The traveller traversing this catchment.

        Returns:
        -------
        str
            A subarea string for the control file.
        """
        areaStr = ""
        for c in code:
            if (c[0] == 1) or (c[0] == 2):
                areaStr += f"{traveller._catchment._vertices[c[1]].area:{self._formattingOptions['area_table']['percision']}},"
        areaStr += '-99'

        values = areaStr.split(',')
        formatted_values = (
            f"{resources.rorb.AREA_TABLE_HEADER}"
            f"{self._makeTable(values, 'area_table')}"
        )

        return formatted_values

    def _fracImpStr(self, code: list, traveller: Traveller) -> str:
        """Format the fraction impervious string according to the RORB manual.

        Parameters
        ----------
        code : tuple
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the traveller when the command code was created.

        traveller : Traveller
            The traveller traversing this catchment.

        Returns:
        -------
        str
            A fraction impervious string for the control file.
        """
        fStr = f"{resources.rorb.FI_TABLE_HEADER} 1,"
        for c in code:
            if (c[0] == 1) or (c[0] == 2):
                fStr += f"{traveller._catchment._vertices[c[1]].fi:{self._formattingOptions['fi_table']['percision']}},"
        fStr += ' -99'

        values = fStr.split(',')
        formatted_values = (
            f"{values[0]} ,\n"
            f"{self._makeTable(values[1:], 'fi_table')}"
        )

        return formatted_values

    def _makeTable(self, value: list, table: str) -> str:
        """Format a table string according to the RORB manual.

        Parameters
        ----------
        value : list
            A list of values to be formatted.
        table : str
            The name of the table to be formatted.

        Returns:
        -------
        str
            A formatted table string for the control file.
        """
        formatted_values = ""
        for i, val in enumerate(value[:-1]):
            if (i % 5 == 0) and (i != 0):
                formatted_values += f"\n{val:{self._formattingOptions[table]['column_width']}},"
            else:
                formatted_values += f"{val:{self._formattingOptions[table]['column_width']}},"
        formatted_values += f"\n{value[-1]}"

        return formatted_values

    @property
    def state(self):
        return (self._stateVector)

    @state.setter
    def state(self):
        raise AttributeError('State vector is generated not set')

class GraphicsBlock():
    """Builds the graphics block for the RORB control file.
    """

    def __init__(self) -> None:
        self._idMap = {}
        self._nodeVector = []
        self._reachVector = []
        self._nodeID = self._idGenerator()
        self._reachID = self._idGenerator()

        resources_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'resources')
        with open(os.path.join(resources_dir, 'formatting.json'), 'r') as f:
            self._formattingOptions = json.load(f)

    def step(self, code: tuple, traveller: Traveller) -> None:
        """Determine graphical information at each catchment position while travelling.

        Parameters
        ----------
        code : Tuple[int, int]
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the traveller when the command code was created.

        traveller : Traveller
            The traveller traversing this catchment.
        """
        self._nodeDisplay(code, traveller)
        self._reachDisplay(code, traveller)

    def build(self, *, title: str = "REACH", version: str = "6.45", preserve_aspect: bool = False) -> str:
        """Build the graphical block string for the .catg file.
        
        Returns:
        -------
        str
            The graphical block string for the .catg file.
        """
        self._replaceIDTags(self._nodeVector)
        self._replaceIDTags(self._reachVector)
        self._normalizeCoordinates(preserve_aspect=preserve_aspect)
        if version == "6.45":
            header = resources.rorb.GRAPHICAL_HEADER.replace("REACH\n", f"{title}\n", 1)
        elif version == "002":
            header = (
                f"{title}\nC RORB_GE 002\nC\nC {title}\nC\n"
                "C #FILE COMMENTS\nC   0\nC\nC #SUB-AREA AREA COMMENTS\nC   0\nC\n"
                "C #IMPERVIOUS FRACTION COMMENTS\nC   0\nC\nC #BACKGROUND IMAGE\nC  T  F\nC\n"
            )
        else:
            raise ValueError(f"Unknown graphical export version {version!r}")

        graphicalStr = (
            f"{header}"
            f"{self._generateNodeString()}"
            f"{resources.rorb.LEADING_TOKEN}\n"
            f"{self._generateReachString()}"
            f"{resources.rorb.GRAPHICAL_TAIL}"
        )

        return graphicalStr

    def _replaceIDTags(self, vector: list) -> None:
        """Replace the ID tags in the vector with the ID generated by the ID generator.
        
        Parameters
        ----------
        vector : list
            The vector to replace the ID tags in.
        """
        for i, row in enumerate(vector):
            for k, v in row.items():
                if v in self._idMap:
                    vector[i][k] = self._idMap[v]

    def _normalizeCoordinates(self, scale: float = 90.0, shift: float = 2.5, *, preserve_aspect: bool = False) -> None:
        """Normalize the coordinates of the catchment to fit within the RORB GE window.

        Parameters
        ----------
        scale : float
            The scale factor to apply to the coordinates.
        shift : float
            The shift factor to apply to the coordinates.
        """
        if not self._nodeVector:
            return
        xs = [row["x"] for row in self._nodeVector]
        ys = [row["y"] for row in self._nodeVector]
        min_x, min_y = min(xs), min(ys)
        range_x, range_y = max(xs) - min_x, max(ys) - min_y
        shared = max(range_x, range_y)
        if shared <= 0:
            raise ValueError("Graphical node coordinate extent must be nondegenerate")
        denom_x = shared if preserve_aspect else range_x
        denom_y = shared if preserve_aspect else range_y
        for row in (*self._nodeVector, *self._reachVector):
            row["x"] = (row["x"] - min_x) / denom_x * scale + shift if denom_x else shift
            row["y"] = (row["y"] - min_y) / denom_y * scale + shift if denom_y else shift

    def _generateNodeString(self) -> str:
        """Generates the display information string for the nodes.

        Returns:
            A formated display string of the node data, compatible with RORB GE.
        """
        nodeStr = resources.rorb.NODE_HEADER
        nodeStr += f"{resources.rorb.LEADING_TOKEN}{len(self._nodeVector):>7}\n"

        for row in self._nodeVector:
            nodeStr += resources.rorb.LEADING_TOKEN
            for item in row:
                nodeStr += f"{row[item]:{self._formattingOptions['node'][item]}}"
            nodeStr += f"\n{resources.rorb.LEADING_TOKEN}\n"

        return nodeStr

    def _generateReachString(self) -> str:
        """Generates the display information string for the reaches.

        Returns:
            A formated display string of the reach data, compatible with RORB GE.
        """
        reachStr = resources.rorb.REACH_HEADER
        reachStr += f"{resources.rorb.LEADING_TOKEN}{len(self._reachVector):>7}\n"

        for row in self._reachVector:
            reachStr += resources.rorb.LEADING_TOKEN
            for item in row:
                if (item == 'x') or (item == 'y'):
                    reachStr += f"\n{resources.rorb.LEADING_TOKEN}"
                reachStr += f"{row[item]:{self._formattingOptions['reach'][item]}}"
            reachStr += "\n"

        return reachStr

    def _nodeDisplay(self, code: tuple, traveller: Traveller) -> None:
        """Add display information for a node to the node vector.

        The node vector holds the ordered information (in order of travel) for each node in the catchment.

        Parameters
        ----------
        code : Tuple[int, int]
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the node to be displayed.

        traveller : Traveller
            The traveller traversing this catchment.
        """
        pos = code[1]
        if code[0] in (1, 2, 5):
            node = traveller.getNode(pos)
            x, y = node.coordinates()
            prnt = 70 if isinstance(node, Confluence) and node.isOut else 0

            ds_node = traveller.getNode(traveller.down(pos))
            ds_name = f"<{ds_node.name}>"

            # Order according to the column order in the control vector.
            data = {
                'id': f"<{node.name}>",
                'x': x,
                'y': y,
                'icon': 1,
                'basin': int(isinstance(node, Basin)),
                'end': int(node.isOut) if isinstance(node, Confluence) else 0,
                'ds': ds_name,
                'name': f" {node.name}",
                'area': node.area if isinstance(node, Basin) else 0,
                'fi': node.fi if isinstance(node, Basin) else 0,
                'print': prnt,
                'excess': 0,
                'comment': 0
            }

            self._idMap[data['id']] = next(self._nodeID)
            self._nodeVector.append(data)

    def _reachDisplay(self, code: tuple, traveller: Traveller) -> None:
        """Add display information for a reach to the reach vector.

        The reach vector holds the ordered information (in order of travel) for each reach in the catchment.

        Parameters
        ----------
        code : Tuple[int, int]
            A coded tuple with:

            [0] - The command code.
            [1] - The position of the reach to be displayed.

        traveller : Traveller
            The traveller traversing this catchment.
        """
        pos = code[1]
        if code[0] in (1, 2, 5):
            try:
                reach = traveller.getReach(pos)
                ep = reach.getEnd().coordinates()
                sp = reach.getStart().coordinates()
                x = (ep[0] - sp[0]) / 2 + sp[0]
                y = (ep[1] - sp[1]) / 2 + sp[1]

                # Order according to the column order in the control vector.
                data = {
                    'id': f"<{reach.name}>",
                    'name': f" {reach.name}",
                    'us': f"<{traveller.getNode(pos).name}>",
                    'ds': f"<{traveller.getNode(traveller.down(pos)).name}>",
                    'translation': 0,
                    'type': reach.type.value,
                    'print': 0,
                    'length': reach.length() / 1000,
                    'slope': reach.slope,
                    'npoints': 1,
                    'comment': 0,
                    'x': x,
                    'y': y,
                }

                self._idMap[data['id']] = next(self._reachID)
                self._reachVector.append(data)

            except KeyError:
                pass

    @staticmethod
    def _idGenerator():
        """Generate a unique ID for each node and reach in the catchment.
        """
        i = 0
        while True:
            i += 1
            yield i

class RORB(Model):
    """Create a RORB GE control vector for input to the RORB runoff routing model.
    """

    def __init__(self):
        pass

    def getVector(self, traveller: Traveller) -> str:
        traveller.next()
        vectorBlock = VectorBlock()
        graphicBlock = GraphicsBlock()

        while(traveller._pos != traveller._endSentinel):
            vectorBlock.step(traveller)
            graphicBlock.step(vectorBlock.state[-1], traveller)

        return graphicBlock.build() + vectorBlock.build(traveller)

def render_ordered_catg(
    title: str,
    controls: Sequence[OrderedControl],
    areas_km2: Sequence[Decimal],
    impervious_fractions: Sequence[Decimal],
    *,
    explicit_reach_types: bool,
    nodes: Sequence[OrderedNode] = (),
    reaches: Sequence[OrderedReach] = (),
) -> str:
    """Public ordered RORB_GE writer, independent of GIS traversal.

    Graphical XY is display-only. Physical lengths come exclusively from the
    ordered controls and explicit graphical-reach length fields. No generated
    control is inferred from normalized display coordinates.
    """
    title = _ordered_label(title, "Catchment title")
    if not title:
        raise ValueError("Catchment title cannot be empty")
    vector = VectorBlock().build_ordered(controls, areas_km2, impervious_fractions,
                                         explicit_reach_types=explicit_reach_types)
    if not nodes and not reaches and not explicit_reach_types:
        return (
            f"{title}\nC RORB_GE 002\nC #NODES\nC 0\nC #REACHES\nC 0\n"
            "C #STORAGES\nC 0\nC #INFLOW/OUTFLOW\nC 0\nC END RORB_GE\nC\n" + vector
        )
    if not nodes or not reaches or len(reaches) != len(nodes) - 1:
        raise ValueError("Edited RORB_GE 6.45 requires explicit ordered graphical nodes and reaches")
    if [n.identifier for n in nodes] != list(range(1, len(nodes) + 1)):
        raise ValueError("Graphical node IDs must be consecutive and source ordered")
    if [r.identifier for r in reaches] != list(range(1, len(reaches) + 1)):
        raise ValueError("Graphical reach IDs must be consecutive and source ordered")
    if sum(n.outlet for n in nodes) != 1:
        raise ValueError("Exactly one graphical outlet print 70 is supported")
    ids = {n.identifier for n in nodes}
    for node in nodes:
        if not math.isfinite(node.x) or not math.isfinite(node.y) or node.downstream not in (*ids, 0):
            raise ValueError(f"Graphical node {node.identifier} has invalid XY or downstream reference")
        _ordered_number(node.area_km2, "Graphical node area", places=6)
        _ordered_number(node.impervious_fraction, "Graphical node impervious fraction", places=6)
        if node.impervious_fraction > 1:
            raise ValueError("Graphical node impervious fraction exceeds 1")
        _ordered_label(node.name, "Graphical node name")
    for reach in reaches:
        if (reach.upstream not in ids or reach.downstream not in ids
                or not math.isfinite(reach.x) or not math.isfinite(reach.y)):
            raise ValueError(f"Graphical reach {reach.identifier} has invalid endpoints or XY")
        if reach.length_km <= 0:
            raise ValueError("Graphical reach length must be positive")
        _ordered_number(reach.length_km, "Graphical reach length", places=3)
        _ordered_label(reach.name, "Graphical reach name")
    graphics = GraphicsBlock()
    graphics._nodeVector = [{
        "id": n.identifier, "x": n.x, "y": n.y, "icon": 1.0, "basin": int(n.source),
        "end": int(n.outlet), "ds": n.downstream, "name": f" {n.name}",
        "area": n.area_km2, "fi": n.impervious_fraction,
        "print": 70 if n.outlet else 0, "excess": 0, "comment": 0,
    } for n in nodes]
    graphics._reachVector = [{
        "id": r.identifier, "name": f" {r.name}", "us": r.upstream,
        "ds": r.downstream, "translation": 0, "type": 1, "print": 0,
        "length": r.length_km, "slope": 0.0, "npoints": 1, "comment": 0,
        "x": r.x, "y": r.y,
    } for r in reaches]
    return graphics.build(title=title, version="6.45" if explicit_reach_types else "002",
                          preserve_aspect=True) + vector

