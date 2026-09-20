import { useMemo, useState } from "react";
import { api, type RecallApiPollCombo } from "../lib/api";
import { PageError } from "../lib/PageError";
import { SearchBox, useSearch } from "../lib/search";
import { SortIndicator, useSort } from "../lib/sort";
import { useFetch } from "../lib/useFetch";

type SortKey = "fleet_vehicles" | "model_year" | "last_campaign_count";

/**
 * The live recall feed — `api.nhtsa.gov/recalls/recallsByVehicle`, polled per fleet combo.
 *
 * This view exists because the integration did not have one. It has run since Phase 1 and
 * was read by nothing: no route, no view, no dashboard widget, no test. The polling job
 * called its output "the reactive product surface" and the only people who ever saw it were
 * whoever opened that job's run log.
 *
 * **The design problem here is the empty state, not the full one.** For weeks every campaign
 * the API returned was already in the daily flat file, so there were zero alerts — and "the
 * feed is healthy and there is no news" must not look like "the feed is broken". So the
 * coverage panel is always populated and always shown, and a zero-alert result is stated in
 * a sentence rather than rendered as an empty table. On 2026-09-20 the first sweep in twenty
 * days found two real novel campaigns, so both states are live states, not hypotheticals.
 */
export function RecallApi() {
  const { data, error, gated } = useFetch(() => api.recallApiStatus(), []);
  const [failuresOnly, setFailuresOnly] = useState(false);

  const combos = useMemo(() => {
    const all = data?.poll ?? [];
    return failuresOnly ? all.filter((c) => c.last_status !== "ok") : all;
  }, [data, failuresOnly]);

  const {
    query,
    setQuery,
    filtered: searched,
  } = useSearch(
    combos,
    (c) => `${c.make} ${c.model} ${c.model_year} ${c.last_status ?? ""}`,
  );
  const { sorted, sortKey, sortDir, toggleSort } = useSort<RecallApiPollCombo, SortKey>(
    searched,
    (c, key) => c[key] ?? 0,
  );

  if (gated)
    return (
      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Sign-in required</h3>
        <p className="muted" style={{ marginBottom: 0 }}>
          The recall feed is read under your Databricks identity. The <strong>Evidence</strong>{" "}
          tab needs no session.
        </p>
      </div>
    );

  if (!data && error)
    return (
      <>
        <div className="page-head">
          <h2>Recall API</h2>
          <p>Live NHTSA recall feed — coverage, health, and campaigns the daily file has not.</p>
        </div>
        <PageError title="Recall API status could not be loaded." detail={error} />
      </>
    );

  if (!data)
    return (
      <>
        <div className="stats">
          {[0, 1, 2, 3].map((i) => (
            <div className="stat" key={i}>
              <div className="skeleton tall" style={{ marginBottom: 0 }} />
            </div>
          ))}
        </div>
        <div className="skeleton wide tall" />
      </>
    );

  const s = data.summary;
  const polled = s.last_polled_at ? new Date(s.last_polled_at).toLocaleString() : "never";
  const healthy = s.success_rate_pct !== null && s.success_rate_pct >= 99;

  return (
    <>
      <div className="page-head">
        <h2>Recall API</h2>
        <p>
          NHTSA&rsquo;s live <code>recallsByVehicle</code> endpoint, swept once per fleet
          make/model/year combination. The daily flat file is the corpus; this is the feed that
          can be ahead of it.
        </p>
      </div>

      <div className="stats">
        <div className={healthy ? "stat is-ok" : "stat is-danger"}>
          {/* Never render null as 0%. "Not polled yet" and "every combo failed" are
              different answers and the API deliberately returns null for the first. */}
          <div className="v">
            {s.success_rate_pct === null ? "—" : `${s.success_rate_pct}%`}
          </div>
          <div className="k">Poll success rate</div>
        </div>
        <div className="stat">
          <div className="v">{s.combos.toLocaleString()}</div>
          <div className="k">Fleet combos polled</div>
        </div>
        <div className="stat">
          <div className="v">{s.fleet_vehicles_covered.toLocaleString()}</div>
          <div className="k">Vehicles covered</div>
        </div>
        <div className={s.alerts > 0 ? "stat is-danger" : "stat is-ok"}>
          <div className="v">{s.alert_campaigns.toLocaleString()}</div>
          <div className="k">Campaigns ahead of the flat file</div>
        </div>
      </div>

      <p className="footnote">Last swept {polled}.</p>

      <div className="panel">
        <h3 style={{ marginTop: 0 }}>Ahead of the daily file</h3>
        {s.alerts === 0 ? (
          <p className="muted" style={{ marginBottom: 0 }}>
            No campaigns found that the daily flat file does not already have. That is a
            result, not an empty screen: the feed polled {s.combos.toLocaleString()} combos
            successfully and the corpus is current. This panel fills only when NHTSA publishes
            a campaign affecting the fleet before the next flat-file drop.
          </p>
        ) : (
          <>
            <p className="muted">
              <strong>{s.alert_campaigns}</strong>{" "}
              {s.alert_campaigns === 1 ? "campaign" : "campaigns"} visible in the live API and
              absent from the daily flat file, together touching{" "}
              <strong>{s.vehicles_exposed_by_alerts.toLocaleString()}</strong> fleet vehicles.
              One campaign can appear on several rows — exposure is counted per model year.
            </p>
            <div className="wrap">
              <table>
                <thead>
                  <tr>
                    <th>Campaign</th>
                    <th>Vehicle</th>
                    <th>Component</th>
                    <th className="num">Vehicles</th>
                    <th className="num">Depots</th>
                  </tr>
                </thead>
                <tbody>
                  {data.alerts.map((a) => (
                    <tr key={a.alert_key}>
                      <td>
                        {a.campaign_number}
                        {a.park_it && <span className="tag parkit">PARK IT</span>}
                      </td>
                      <td>
                        {[a.make, a.model, a.model_year].filter(Boolean).join(" ")}
                      </td>
                      <td className="muted">{a.component ?? "—"}</td>
                      <td className="num">{a.vehicles_exposed.toLocaleString()}</td>
                      <td className="num">{a.depots_affected.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      {error && <div className="error">{error}</div>}

      <div className="filter-row">
        <SearchBox
          query={query}
          onChange={setQuery}
          placeholder="make, model, year, status"
          matched={sorted.length}
          total={data.poll.length}
          label="Search polled combinations"
        />
        <label className="filter-checkbox">
          <input
            type="checkbox"
            checked={failuresOnly}
            onChange={(e) => setFailuresOnly(e.target.checked)}
          />
          Failures only
        </label>
      </div>

      {data.poll.length === 0 ? (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>Nothing polled yet</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            Run <code>fleetguard-poll-recalls-api</code> to populate coverage.
          </p>
        </div>
      ) : sorted.length === 0 ? (
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>No matching combinations</h3>
          <p className="muted" style={{ marginBottom: 0 }}>
            {failuresOnly
              ? "Every polled combination returned successfully."
              : "Nothing matches that search."}
          </p>
        </div>
      ) : (
        <div className="wrap">
          <table>
            <thead>
              <tr>
                <th>Make</th>
                <th>Model</th>
                <th className="num sortable" onClick={() => toggleSort("model_year")}>
                  Year
                  <SortIndicator columnKey="model_year" sortKey={sortKey} sortDir={sortDir} />
                </th>
                <th className="num sortable" onClick={() => toggleSort("fleet_vehicles")}>
                  Fleet vehicles
                  <SortIndicator
                    columnKey="fleet_vehicles"
                    sortKey={sortKey}
                    sortDir={sortDir}
                  />
                </th>
                <th className="num sortable" onClick={() => toggleSort("last_campaign_count")}>
                  Campaigns returned
                  <SortIndicator
                    columnKey="last_campaign_count"
                    sortKey={sortKey}
                    sortDir={sortDir}
                  />
                </th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => (
                <tr key={c.combo_key}>
                  <td>{c.make}</td>
                  <td>{c.model}</td>
                  <td className="num">{c.model_year}</td>
                  <td className="num">{c.fleet_vehicles.toLocaleString()}</td>
                  <td className="num">{c.last_campaign_count ?? "—"}</td>
                  <td className={c.last_status === "ok" ? "" : "risk-high"}>
                    {c.last_status ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="footnote">
        A 400 from this endpoint means NHTSA does not recognise the make/model/year, not that
        the request failed — it answers with HTTP 400 and a body reading &ldquo;Results
        returned successfully&rdquo;. Combos are polled using NHTSA&rsquo;s own model
        vocabulary rather than vPIC&rsquo;s, which is what took coverage from 60% to 200/200.
      </p>
    </>
  );
}
