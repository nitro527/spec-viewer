// Top of the main tab: new versions on 3gpp.org for the downloaded specs, and the bookmarked or annotated
// clauses that changed after the version they were marked in.
import { useEffect, useState } from "react";
import { api, type Job, type SpecInfo, type Updates, type WatchItem } from "../api";

export function KeepUp({ specs, downloading, onJob, onOpen }: {
  specs: SpecInfo[] | null; downloading: boolean; onJob: (j: Job) => void;
  onOpen: (spec: string, clause?: string, version?: string, baseline?: string) => void;
}) {
  const [upd, setUpd] = useState<Updates | null>(null);
  const [checking, setChecking] = useState(false);
  const [watch, setWatch] = useState<WatchItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const check = (refresh = false) => {
    setChecking(true);
    api.updates(refresh).then(setUpd, (e) => setError(String(e).replace(/^Error: /, ""))).finally(() => setChecking(false));
  };
  // the library changed (a download finished): what is new and what changed is different now
  useEffect(() => {
    if (!specs?.length) return;
    check();
    api.watch().then(setWatch, () => undefined);
  }, [specs]);

  if (!specs?.length) return null;
  const newCount = upd?.specs.reduce((n, s) => n + s.new.length, 0) ?? 0;
  const getNew = async () => {
    try {
      onJob(await api.fetchSpecs(upd!.specs.map((s) => s.spec), null, false, true));
    } catch (e) {
      setError(String(e).replace(/^Error: /, ""));
    }
  };
  const seen = (w: WatchItem) => api.watchSeen(w.spec, w.clause_key).then(setWatch, () => undefined);
  const seenAll = async () => {
    for (const sp of new Set(watch.map((w) => w.spec))) setWatch(await api.watchSeen(sp));
  };

  return (
    <section className="keepup">
      <div className="keepup-row">
        {newCount > 0 ? (
          <span>
            <b>3GPP 에 새 버전 {newCount}개</b>{" "}
            <span className="small">{upd!.specs.map((s) => `${s.spec} v${s.new.join(", v")}`).join(" · ")}</span>{" "}
            <button className="primary small" disabled={downloading} onClick={getNew}
                    title="받아 둔 최신 버전보다 새 버전만 받습니다 (예전 버전은 그대로)">
              {downloading ? "받는 중…" : "새 버전 받기"}</button>
          </span>
        ) : (
          <span className="small muted">{upd ? "받은 스펙이 모두 최신입니다" : checking ? "3GPP 새 버전 확인 중…" : ""}</span>
        )}
        <span className="small muted">
          {upd?.checked && ` · 확인 ${upd.checked.replace("T", " ")}`}{" "}
          <button className="link small" disabled={checking} onClick={() => check(true)}>{checking ? "확인 중…" : "다시 확인"}</button>
        </span>
      </div>
      {upd?.errors.length ? <p className="small muted">확인하지 못함: {upd.errors.join(", ")}</p> : null}
      {error && <p className="small status-error">{error}</p>}
      {watch.length > 0 && (
        <details className="watch" open>
          <summary>
            <b>북마크·메모한 절 중 바뀐 것 {watch.length}개</b>
            <span className="small muted"> (표시한 버전 이후 바뀜 · 누르면 그 이후 변경을 색칠해 엽니다)</span>{" "}
            <button className="link small" onClick={(e) => { e.preventDefault(); seenAll(); }}>모두 확인함</button>
          </summary>
          <ul>
            {watch.map((w) => (
              <li key={`${w.spec}/${w.clause_key}`}>
                <button className="link" onClick={() => onOpen(w.spec, w.clause_key, w.latest, w.since ?? undefined)}>
                  TS {w.spec} {w.number && `§${w.number}`} {w.title}</button>
                <span className="small muted">
                  {" "}{w.bookmark ? "★" : "✎"}{" "}
                  {w.status === "removed" ? `v${w.latest} 에 없음 (삭제되었거나 번호가 바뀜)`
                    : `v${w.since} 이후 v${w.changed_in} 에서 바뀜`}
                  {w.note && ` · ${w.note}`}
                </span>{" "}
                <button className="link tiny" title="읽었음: 이제부터 최신 버전과 비교합니다" onClick={() => seen(w)}>확인함</button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
