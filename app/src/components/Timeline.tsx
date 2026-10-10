// Bottom version timeline: click = view that version, Shift+click = set the highlight baseline.
import { cmpVersion, type VersionInfo } from "../api";
import type { VersionColor } from "../colors";

export function Timeline({ versions, version, baseline, colors, onVersion, onBaseline }: {
  versions: VersionInfo[]; version: string; baseline: string; colors: Map<string, VersionColor>;
  onVersion: (v: string) => void; onBaseline: (v: string) => void;
}) {
  const releases = [...new Set(versions.map((v) => v.release))];
  return (
    <div className="timeline-bar" title="클릭: 해당 버전 보기 · Shift+클릭: 변경 표시 기준 버전 지정">
      {releases.map((rel) => (
        <div key={rel} className="rel-group">
          <span className="rel-label">Rel-{rel}</span>
          <div className="rel-dots">
            {versions.filter((v) => v.release === rel).map((v) => {
              const inRange = cmpVersion(v.version, baseline) > 0 && cmpVersion(v.version, version) <= 0;
              return (
                <button key={v.version}
                        className={`vdot ${v.version === version ? "cur" : ""} ${v.version === baseline ? "base" : ""}`}
                        style={{ background: inRange ? colors.get(v.version)?.solid : undefined }}
                        onClick={(e) => (e.shiftKey ? onBaseline(v.version) : onVersion(v.version))}
                        title={`v${v.version}${v.version === baseline ? " (기준)" : ""}`}>
                  <span>{v.version.split(".").slice(1).join(".")}</span>
                </button>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
