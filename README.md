# 🛩️ Zipline Airspace

A public-data survey of the crewed aircraft that fly low across Zipline's Texas operating areas: who flies below 1,200 ft, who broadcasts their position, and how low a public receiver network can hear them.

**Live survey:** [zipline-airspace.vercel.app](https://zipline-airspace.vercel.app) · [Dallas–Fort Worth](https://zipline-airspace.vercel.app/?metro=dfw) · [Austin](https://zipline-airspace.vercel.app/?metro=austin)

Based on public data only.

## Dallas–Fort Worth

The operating area published in the FAA's final environmental assessment for Zipline.

<!-- findings:start -->
- Window: 2026-09-28 to 2026-10-04 (UTC archive days), 168 hours.
- Operating area: 10894 sq mi computed, 10904 stated in the assessment.
- Broadcasting is required below 1,200 ft over 34.4% of the area.
- The feed heard at least three aircraft below 580 ft in 9.7% of map cells, and below 1,200 ft in 50.1%.
- At 22 of 44 airports with enough traffic to check, most arrivals and departures were heard to within 100 ft of the ground.
- On average 2.6 crewed aircraft were in the 80–580 ft band and 7.6 below 1,200 ft, over the whole area.
- 81.8% of that low time was within 3 NM of a public-use runway.
- A receiver on 1090 MHz alone would miss at least 7.2% of observed low time; one on both bands at least 0.5%.
<!-- findings:end -->

## Austin

The proposed operating area in the FAA's draft assessment for the Texas metros. The draft shows it only as a map, so the outline is traced from that map; [METHODS.md](METHODS.md) gives the fit and the area check.

<!-- findings:austin:start -->
- Window: 2026-09-28 to 2026-10-04 (UTC archive days), 168 hours.
- Proposed operating area: traced from the map in the draft assessment, which gives no coordinates. 2498 sq mi computed, 2498 stated; map fitted to 94 airports and heliports, 108 m root mean square.
- Broadcasting is required below 1,200 ft over 4.2% of the area.
- The feed heard at least three aircraft below 580 ft in 11.0% of map cells, and below 1,200 ft in 51.9%.
- At 7 of 9 airports with enough traffic to check, most arrivals and departures were heard to within 100 ft of the ground.
- On average 0.7 crewed aircraft were in the 80–580 ft band and 2.0 below 1,200 ft, over the whole area.
- 83.3% of that low time was within 3 NM of a public-use runway.
- A receiver on 1090 MHz alone would miss at least 6.5% of observed low time; one on both bands at least 0.8%.
<!-- findings:austin:end -->

These lines are written by the pipeline from each metro's `findings.json`. The site reads the same files; no number on it is typed by hand.

## Reproduce

```sh
python3 -m venv .venv && .venv/bin/pip install polars pyarrow numpy shapely pyproj rasterio h3 orjson pytest
make layers METRO=dfw   # FAA airspace and airports, terrain, geoid, METARs
make all METRO=dfw      # downloads seven adsb.lol daily archives (about 4 GB each, deleted after use)
make test
cd web && yarn install && yarn build
```

Use `METRO=austin` for Austin. Methods and limits: [METHODS.md](METHODS.md) · [LIMITS.md](LIMITS.md)

## Licence

Code is MIT. Aggregates derived from adsb.lol are under the Open Database License, with attribution to adsb.lol and its feeders. The published files hold no aircraft addresses, registrations, or hex codes.
