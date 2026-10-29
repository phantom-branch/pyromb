"""Standalone OGR example using the shapefiles included with the repository."""

from pathlib import Path

import pyromb
from osgeo import ogr
from plot_catchment import plot_catchment

DIR = Path(__file__).resolve().parent
DATA_DIR = DIR.parent.parent / "tests" / "data"
REACH_PATH = DATA_DIR / "reaches.shp"
BASIN_PATH = DATA_DIR / "basins.shp"
CENTROID_PATH = DATA_DIR / "centroids.shp"
CONFUL_PATH = DATA_DIR / "confluences.shp"


class OGRVectorLayer(pyromb.VectorLayer):
    """Expose OGR features through Pyromb's existing VectorLayer interface."""

    def __init__(self, path: Path) -> None:
        self.dataset = ogr.Open(str(path))
        if self.dataset is None:
            raise ValueError(f"Cannot open vector file: {path}")
        layer = self.dataset.GetLayer()
        if layer is None:
            raise ValueError(f"No vector layer in {path}")
        self.features = list(layer)

    def geometry(self, i: int) -> list[tuple[float, float]]:
        """Return XY vertices, preserving polygon ring boundaries."""
        geometry = self.features[i].GetGeometryRef()
        if geometry is None:
            return []
        kind = ogr.GT_Flatten(geometry.GetGeometryType())
        if kind == ogr.wkbPolygon:
            # Retain closed interior rings so Builder rejects unsupported holes.
            return [
                (point[0], point[1])
                for index in range(geometry.GetGeometryCount())
                for point in geometry.GetGeometryRef(index).GetPoints()
            ]
        if kind not in (ogr.wkbPoint, ogr.wkbLineString):
            raise ValueError(f"Feature {i} has unsupported geometry: {geometry.GetGeometryName()}")
        return [(point[0], point[1]) for point in geometry.GetPoints()]

    def record(self, i: int) -> dict[str, object]:
        """Return the feature's named attributes."""
        return self.features[i].items()

    def __len__(self) -> int:
        """Return the number of features."""
        return len(self.features)


def main() -> None:
    """Build a sample RORB vector from the tracked catchment shapefiles."""
    plot = False  # Set True to plot the catchment.
    model = pyromb.RORB()

    reach_vector = OGRVectorLayer(REACH_PATH)
    basin_vector = OGRVectorLayer(BASIN_PATH)
    centroid_vector = OGRVectorLayer(CENTROID_PATH)
    confluence_vector = OGRVectorLayer(CONFUL_PATH)

    builder = pyromb.Builder()
    tr = builder.reach(reach_vector)
    tc = builder.confluence(confluence_vector)
    tb = builder.basin(centroid_vector, basin_vector)

    catchment = pyromb.Catchment(tc, tb, tr)
    connected = catchment.connect()
    traveller = pyromb.Traveller(catchment)

    output_path = DIR.parent / ("vector.catg" if isinstance(model, pyromb.RORB) else "runfile.wbn")
    with output_path.open("w") as file:
        file.write(traveller.getVector(model))

    if plot:
        plot_catchment(connected, tr, tc, tb)


if __name__ == "__main__":
    main()
