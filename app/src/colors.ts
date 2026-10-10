// Release-based colour scheme: each release has its own hue band and later
// versions inside a release drift along it (e.g. Rel-16: yellow -> orange).
import { cmpVersion, type VersionInfo } from "./api";

const HUE_BANDS: Record<number, [number, number]> = {
  15: [185, 165], 16: [54, 26], 17: [125, 95], 18: [205, 232], 19: [282, 308], 20: [335, 358], 21: [12, 0],
};

function band(release: number): [number, number] {
  return HUE_BANDS[release] ?? [((release * 47) % 360), ((release * 47) % 360) + 20];
}

export interface VersionColor { version: string; light: string; dark: string; solid: string }

export function versionColors(versions: VersionInfo[]): Map<string, VersionColor> {
  const byRel = new Map<number, VersionInfo[]>();
  for (const v of versions) byRel.set(v.release, [...(byRel.get(v.release) ?? []), v]);
  const out = new Map<string, VersionColor>();
  for (const [rel, list] of byRel) {
    const [h0, h1] = band(rel);
    list.forEach((v, i) => {
      const t = list.length > 1 ? i / (list.length - 1) : 0;
      const h = Math.round(h0 + (h1 - h0) * t);
      out.set(v.version, {
        version: v.version,
        light: `hsl(${h} 95% ${Math.round(84 - 12 * t)}%)`,
        dark: `hsl(${h} 70% 40% / ${(0.35 + 0.3 * t).toFixed(2)})`,
        solid: `hsl(${h} 85% 48%)`,
      });
    });
  }
  return out;
}

/** Visible highlight range: versions strictly after `baseline` and up to `current`. */
export function highlighted(versions: VersionInfo[], baseline: string, current: string): VersionInfo[] {
  return versions.filter((v) => cmpVersion(v.version, baseline) > 0 && cmpVersion(v.version, current) <= 0);
}

export function highlightCss(colors: Map<string, VersionColor>, shown: VersionInfo[], scope: string): string {
  const light: string[] = [], dark: string[] = [];
  for (const v of shown) {
    const c = colors.get(v.version);
    if (!c) continue;
    const sel = `${scope} [data-v="${v.version}"]`;
    light.push(`${sel}{background:${c.light}}`);
    dark.push(`${sel}{background:${c.dark}}`);
    light.push(`${scope} [data-bv="${v.version}"]{--bar:${c.solid}}`);
  }
  return `${light.join("\n")}\n@media (prefers-color-scheme: dark){${dark.join("\n")}}`;
}
