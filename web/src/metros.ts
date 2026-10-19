/** The metros on the site. Numbers come from each metro's findings.json; this holds names and paths only. */
export interface Metro {
  key: "dfw" | "austin";
  /** Label on the switch. */
  label: string;
  /** Place name used in sentences. */
  name: string;
  /** Folder of the metro's data files, relative to the site root. */
  data: string;
  /** The airport that reception distances are measured from. */
  hub: { id: string; name: string };
  /** Airports labelled on the reception chart. */
  named: string[];
  /** True when the boundary is traced from a draft map, not published coordinates. */
  traced: boolean;
}

export const METROS: Metro[] = [
  {
    key: "dfw",
    label: "DFW",
    name: "Dallas–Fort Worth",
    data: "data/",
    hub: { id: "DFW", name: "DFW airport" },
    named: ["DFW", "FTW", "AFW", "TKI", "GVT", "LUD", "RBD"],
    traced: false,
  },
  {
    key: "austin",
    label: "Austin",
    name: "Austin",
    data: "data/austin/",
    hub: { id: "AUS", name: "Austin-Bergstrom" },
    named: ["AUS", "EDC", "GTU", "HYI", "RYW", "T74", "BAZ"],
    traced: true,
  },
];

const SKIP_WELCOME = "airwatch-switched";

/** The metro named by ?metro= or by the hash (#austin). Null means the overview of every metro. */
export function metroFromUrl(): Metro | null {
  const params = new URLSearchParams(window.location.search);
  const wanted = (params.get("metro") ?? window.location.hash.slice(1)).toLowerCase();
  return METROS.find((metro) => metro.key === wanted) ?? null;
}

/** A link to `metro` on this page. */
export function metroHref(metro: Metro): string {
  return `${window.location.pathname}?metro=${metro.key}`;
}

/** A link to the overview, which picks no metro. */
export function overviewHref(): string {
  return window.location.pathname;
}

/** Remember that the reader picked a metro on the page, so the next page skips the welcome card. */
export function markSwitch(): void {
  try {
    window.sessionStorage.setItem(SKIP_WELCOME, "1");
  } catch {
    // Storage blocked: the welcome card shows again, which is harmless.
  }
}

/** True once, on the page the switch opened. */
export function cameFromSwitch(): boolean {
  try {
    const set = window.sessionStorage.getItem(SKIP_WELCOME) === "1";
    window.sessionStorage.removeItem(SKIP_WELCOME);
    return set;
  } catch {
    return false;
  }
}
