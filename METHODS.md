# Methods

Every number on the site is read from the metro's `findings.json` (`web/public/data/` for Dallas–Fort Worth, `web/public/data/austin/` for Austin), which `make all` writes. Thresholds live in `pipeline/survey/constants.py` and were fixed before looking at results. They are the same for both metros. What differs between metros (boundary, weather stations, terrain tiles, file locations) is in `pipeline/survey/metros.py`.

## Data

Aircraft positions come from the adsb.lol `globe_history_2026` daily archives: the production release and the mlat-only release, for the UTC days listed on the page. Both metros use the same seven days, and each day is read once for both.

The Dallas–Fort Worth operating area is the quadrilateral in footnote 12 of the Dallas–Fort Worth final environmental assessment. The printed northeast longitude `-95924358` is read as `-95.924358`; the computed area is checked against the 10,904 sq mi the assessment states.

The Austin area is the proposed operating area in the FAA's draft environmental assessment for the Texas metros (11 September 2026). The draft publishes no coordinates for it, only a map, so it is traced from that map. See below. It is a draft boundary, not a final decision.

Archive days are UTC and the page reports hours in Central Time, so the first and last local days are partial. Rates divide by the hours the window actually holds, not by a count of calendar days.

## Tracing the Austin boundary

Appendix H of the draft (PDF page 194, saved as `data/sources/texas_draft_ea_p194.jpg`) is a 1,660 by 1,266 pixel map at 1:1,025,400, about 166 m a pixel. It draws every airport and heliport as a brown dot and the Austin and San Antonio study areas as red outlines. `python -m survey.georef` does the following and writes `data/sources/texas_boundary_trace.json`.

1. Dots are found by colour. Only dots that stand alone are kept, since touching dots have no clean centre: 114 of them.
2. Seven labelled towns give a rough starting fit. Each dot is then paired with the nearest FAA NASR landing facility, keeping only pairs that are each other's nearest, and an affine transform is refitted as the search radius shrinks from 25 to 3 pixels. 94 dots pair up: 59 airports, 34 heliports and one ultralight field.
3. The fit is repeated in seven map projections. Texas Centric Lambert Conformal fits best: the matched dots sit 108 m from their NASR positions (root mean square), 302 m at most. Texas Centric Albers and UTM zone 14N fit almost as well. Web Mercator and plain longitude and latitude fit clearly worse, 199 m and 167 m, so the map is not drawn in either.
4. Each edge of the outline is a straight line fitted through the red pixels within 5 pixels of it. Corners are where neighbouring lines meet. Austin's south edge runs along San Antonio's north edge west of San Marcos, so it is fitted only east of there.

**Error.** Resampling the matched dots moves Austin's corners by up to 59 m (95th percentile). Fitting in the other projections that fit about as well moves them by up to 98 m. Treat the boundary as good to about 100 m.

**Area check.** The traced outline is 2,498.1 sq mi, against 2,498 sq mi in Table 2.2-1 of the draft. Moving every edge one pixel out or in gives 2,477 to 2,519 sq mi, so the match is closer than the tracing can promise, but it rules out a gross error such as the wrong projection or a misread scale. San Antonio, traced the same way, comes to 3,326.2 against 3,326 stated. San Antonio is not surveyed here.

## Height above ground

Geometric altitude is treated as height above the WGS84 ellipsoid. Height above ground is that value, minus the GEOID18 undulation, minus USGS 1 arc-second bare-earth elevation. Where geometric altitude is missing, barometric altitude is shifted by `(altimeter − 29.92) × 1,000 ft` using the nearest hourly METAR, then the terrain is subtracted. Points with neither are not counted.

**Check.** On-ground reports within 200 m of a NASR runway end are compared with the published runway elevation. The fleet uses whichever reading of the geometric field (ellipsoid or mean sea level) has the smaller mean absolute residual; the ellipsoid reading is kept unless the other is better by more than 25 ft. An aircraft with at least eight matched reports can take the other reading on its own residuals. The page shows the residual distribution.

**Sensitivity.** Every band is recomputed with both edges moved up and down 100 ft. A share that moves by more than 5 percentage points is reported as inconclusive. Altimeter temperature error is not modelled separately and sits inside that shift.

## Time in a band

Consecutive reports from one aircraft are integrated. A gap counts as at most 30 seconds, so a reception dropout is not counted as presence; the median gap between reports is under 3 seconds. Ground reports and stale positions are not integrated.

Totals are also given as the **average number of aircraft in the band**: aircraft-seconds divided by the seconds in the window. It is the number you would expect to find at a random moment, and it does not grow with the length of the window.

The **cruise-adjacent band** is 80–580 ft above the ground: the 330 ft cruise in the assessment, plus and minus the 250 ft vertical distance in the encounter-reporting condition of Exemption No. 19111F. The Texas draft gives the same 330 ft cruise. It is an altitude band over the whole area, not a count of encounters. The **low band** is the surface to 1,200 ft, high enough to include a light-aircraft traffic pattern.

Aircraft class comes from the ADS-B emitter category. A missing category with a known helicopter type code is a rotorcraft. Surface vehicles and category B6 (unmanned) are left out.

## How low the feed hears

Two measures, because one alone is misleading.

**By map cell.** A cell is an H3 resolution-7 hexagon, about 5 km². It is marked *heard at drone height* when at least three different aircraft were heard there below 580 ft, and *heard below 1,200 ft* when at least three were heard below that. Otherwise it is *only heard higher* or *nothing heard*. This shows where low reception is proven. It cannot tell a quiet sky from a deaf receiver: a cell with no low traffic stays unproven however good the reception is.

**At airports.** Aircraft at an airport are known to go all the way to the ground, so the height at which the feed last hears them is a direct reading of its floor there. A visit is one aircraft, on one local day, heard below 1,000 ft within 1.5 NM of a runway end, whose track starts, ends, or breaks for two minutes there. That condition keeps arrivals and departures and drops aircraft that fly past. For each airport with at least five visits the page gives the median lowest height heard and the share of visits heard to within 100 ft of the ground. Airports with no surveyed runway ends in NASR use the airport reference point. Distances are measured from DFW airport, or from Austin-Bergstrom for Austin.

## Rules and zones

At low altitude, ADS-B Out is required for an aircraft with an engine-driven electrical system inside the Mode C veil, or inside Class B or C airspace whose floor is at or below 1,200 ft (14 CFR 91.225). Aircraft without that electrical system are exempt in the veil outside Class B and C. Austin has no Mode C veil, since the FAA draws one only around Class B airports. There the rule at low altitude covers the Class C surface area around Austin-Bergstrom; the outer Class C shelf starts at 2,100 ft MSL, about 1,550 ft above the airport, so it is left out. As for Dallas–Fort Worth, a floor given above sea level is turned into height above ground with the terrain at the centre of the airspace shape.

The public-runway stand-off is 3 NM from the centreline of every public-use runway, the distance in condition 18b of the exemption. The exemption is not tied to a metro: its conditions cover every aircraft in Zipline's operations specifications, so the same stand-off and the same 250 ft encounter distance are used for Austin. Public-use airports with no surveyed runway ends are buffered from their reference point. Time outside the stand-off is split, without double counting, into: within 3 NM of a private-use runway, within 1 NM of a medical-use heliport, within 1 NM of any other heliport, and everywhere else.

**Busy areas away from public runways** are groups of adjacent cells, counting only time outside the stand-off and inside the cruise-adjacent band, where each cell holds at least ten aircraft-minutes from at least three aircraft. Each group is placed relative to the nearest public-use airport, and named after a private strip or heliport if one lies within 2 NM.

## Which radio link

Each aircraft is classed, per UTC day, by the best link it was seen on anywhere in the study box:

- **1090 MHz ADS-B** if any report is direct ADS-B.
- **UAT (978 MHz)** if it appears only as ADS-R, the ground rebroadcast of a 978 MHz broadcaster. A 1090-only receiver in the air does not hear these aircraft directly.
- **No broadcast** if it appears only as TIS-B (a radar-derived target) or only by multilateration (a Mode S transponder with no ADS-B position).

Two shares follow. A receiver on 1090 MHz alone misses UAT plus no-broadcast time; a receiver on both bands misses only no-broadcast time. Both are lower bounds: aircraft with no transponder never enter the feed, and UAT aircraft outside ADS-R coverage are not seen at all.

## Feed health

The page lists, per UTC day, the points received and the number of distinct large and light aircraft. Airline traffic is steady from day to day, so a drop in large aircraft would point to the feed, while a swing in light aircraft alone points to flying weather. Hours with under a quarter of the typical volume for that hour of day are flagged.

## The 10-mile brief

Clicking the map sums the cells whose centres lie within 10 statute miles of the point, the service radius of one site in the Dallas–Fort Worth assessment. The Texas draft gives no radius but limits each delivery round trip to 20 miles; Austin uses the same 10 miles. It is a property of the airspace around any point. It says nothing about where Zipline has sites.

## Privacy

The published files hold no aircraft addresses, registrations, or hex codes. The one-day track replay leaves out aircraft flagged military, LADD, or PIA; they stay in the aggregates.
