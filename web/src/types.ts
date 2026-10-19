export interface BandSummary {
  aircraft_hours: number;
  distinct_aircraft: number;
  average_present: number;
  hours_per_day: number;
  by_class: Record<string, number>;
  by_hour_present: number[];
  weekday_present: number | null;
  weekend_present: number | null;
}

export interface ZoneShare {
  hours: number;
  share_of_low: number | null;
  share_of_remainder: number | null;
  by_class: Record<string, number>;
}

export interface Place {
  id: string;
  name: string;
  nm: number;
  dir: string;
}

export interface Hotspot {
  lat: number;
  lon: number;
  cells: string[];
  hours: number;
  minutes_per_day: number;
  aircraft: number;
  by_class: Record<string, number>;
  top_class: string | null;
  busiest_3h_start: number;
  busiest_3h_share: number;
  nearest_public: Place | null;
  nearest_pad: Place | null;
}

export interface AirportCheck {
  id: string;
  name: string;
  use: string;
  lon: number;
  lat: number;
  visits: number;
  median_lowest_ft: number;
  to_ground_share: number;
  hub_nm: number;
}

export interface LinkSide {
  hours: number;
  dual_band_miss_share: number | null;
  uat_share: number | null;
}

export interface Residuals {
  n: number;
  mae_ft: number | null;
  p50_ft: number | null;
  p90_abs_ft: number | null;
}

export interface SensitivityRun {
  cruise_hours: number;
  low_hours: number;
  public_runway_share: number | null;
  single_band_miss_share: number | null;
  dual_band_miss_share: number | null;
  rotorcraft_share_of_cruise: number | null;
}

/** How a boundary with no published coordinates was traced from the assessment's map. */
export interface TracedBoundary {
  method: "traced";
  source: string;
  note: string;
  corners: [number, number][];
  map_projection: string;
  metres_per_pixel: number;
  dots_found: number;
  dots_matched: number;
  residual_rms_m: number;
  residual_max_m: number;
  corner_shift_bootstrap_p95_m: number;
  corner_shift_other_projections_m: number;
  area_if_edges_move_1px_sq_mi: [number, number];
}

export interface Findings {
  study: {
    name: string;
    window: [string, string];
    timezone: string;
    area: { computed_sq_mi: number; stated_sq_mi: number; delta_pct: number; within_half_percent: boolean };
    /** Published boundaries: how the printed corners were read. */
    longitude_correction?: string;
    /** Traced boundaries. */
    boundary?: TracedBoundary;
    replay_date: string;
    low_band_ft: [number, number];
    cruise_band_ft: [number, number];
    cruise_ft: number;
    enroute_ceiling_ft: number;
    runway_buffer_nm: number;
    site_radius_mi: number;
    classes: string[];
    links: string[];
  };
  window: {
    utc_days: string[];
    hours: number;
    by_hour: number[];
    weekday_hours: number;
    weekend_hours: number;
    local_start: string;
    local_end: string;
  };
  health: {
    days: { date: string; points: number; aircraft: number; large_aircraft: number; light_aircraft: number; tracked_hours: number }[];
    thin_hours: { utc: string; points: number; typical: number }[];
    hours_checked: number;
  };
  calibration: {
    basis: string;
    chosen?: Residuals;
    histogram?: { lo_ft: number; n: number }[];
    per_aircraft_msl_n?: number;
    runway_matches?: number;
    points?: number;
    agl_geometric_share?: number;
    agl_barometric_share?: number;
    agl_missing_share?: number;
  };
  q1: {
    area: {
      operating_sq_mi: number;
      mandatory_sq_mi: number;
      mandatory_share: number;
      carveout_share: number;
      outside_rule_share: number;
      veil_sq_mi: number;
      class_b_low_sq_mi: number;
      class_c_low_sq_mi: number;
      class_d_surface_sq_mi: number;
      medical_heliports: number;
      heliports: number;
      public_airports: number;
    };
    traffic: { low_band_hours: number; share_where_required: number | null; share_outside_rule: number | null };
  };
  q2: {
    cells: number;
    min_support: number;
    tiers: { cruise: number; low: number; higher: number; none: number };
    heard_cruise_share: number;
    heard_low_share: number;
    not_shown_share: number;
    airports: {
      checked: number;
      tracked_to_ground: number;
      min_visits: number;
      radius_nm: number;
      ground_ft: number;
      table: AirportCheck[];
      by_distance: { lo_nm: number; hi_nm: number; airports: number; visits: number; median_of_medians_ft: number; to_ground_share: number }[];
    };
  };
  q3: { cruise: BandSummary; low: BandSummary; min_distinct_for_a_cell: number };
  q4: {
    low_band_hours: number;
    public_runway_3nm: ZoneShare;
    remainder_hours: number;
    remainder_by_class: Record<string, number>;
    private_runway_3nm: ZoneShare;
    hospital_1nm: ZoneShare;
    other_heliport_1nm: ZoneShare;
    elsewhere: ZoneShare;
    class_d_share_of_remainder: number | null;
    medical_heliports: number;
    heliports: number;
    hotspots: Hotspot[];
  };
  q5: {
    low_band_hours: number;
    by_link: Record<string, { hours: number; share: number | null }>;
    aircraft_by_link: Record<string, number>;
    by_class: Record<string, Record<string, number | null>>;
    dual_band_miss_share: number | null;
    single_band_miss_share: number | null;
    uat_share: number | null;
    inside_rule: LinkSide;
    outside_rule: LinkSide;
  };
  sensitivity: { shift_ft: number; flip_pp: number; runs: Record<"minus" | "nominal" | "plus", SensitivityRun>; inconclusive: string[] };
  excluded_non_crewed_hours: number;
}

/** A map cell as the pipeline writes it. Short keys keep the file small. */
export interface RawCell {
  h: string;
  lat: number;
  lon: number;
  /** Reception tier: 0 heard at drone height, 1 heard below 1,200 ft, 2 only heard higher, 3 nothing heard. */
  t: number;
  /** 1 where ADS-B Out is required at low altitude. */
  v: number;
  /** 1 inside the public-runway stand-off. */
  b: number;
  lh?: number;
  ln?: number;
  cn?: number;
  /** Low band: [class * 24 + hour, seconds, ...]. */
  lx?: number[];
  /** Cruise-adjacent band, same encoding. */
  cx?: number[];
  /** Low band by link: [link, seconds, ...]. */
  k?: number[];
  /** Low band by 100 ft bin: [bin, seconds, ...]. */
  p?: number[];
  /** Low-band seconds inside the public-runway stand-off. */
  so?: number;
}

export interface TrackFile {
  date: string;
  classes: string[];
  tracks: { c: number; p: number[] }[];
}

export type Band = "cruise" | "low";
export type View = "traffic" | "reception" | "link";
export type ClassGroup = "all" | "rotorcraft" | "light" | "larger";
