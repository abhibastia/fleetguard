const GITHUB_MARK = (
  <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" fill="currentColor">
    <path d="M8 0C3.58 0 0 3.58 0 8a8 8 0 0 0 5.47 7.59c.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
  </svg>
);

const PROVIDER_COPY: Record<string, { label: string; mark: React.ReactNode; note: string }> = {
  github: {
    label: "Continue with GitHub",
    mark: GITHUB_MARK,
    note:
      "Signing in gives you read access to the queue, emerging signals and evidence. Approving " +
      "a service campaign additionally requires being on this deployment's approver list — " +
      "signing in proves who you are, not that you may dispatch work orders against a fleet.",
  },
  databricks: {
    label: "Continue with Databricks",
    mark: null,
    note:
      "Signing in with your Databricks account gives you a real Databricks identity for this " +
      "session — the same one Unity Catalog and Postgres row-level security evaluate against, " +
      "not an app-only login. This deployment reads and writes live data.",
  },
};

/**
 * The sign-in gate for the public deployment.
 *
 * It states plainly what signing in does and does not get you. A login screen that implies
 * full operator powers, on a deployment that serves a read-only snapshot, would be the
 * interface telling its first lie before the user has even entered. Which provider is shown
 * comes from the backend's `/auth/status` (E-13's seam, seen from this side) rather than
 * being hardcoded here, so this component renders correctly under either
 * `FLEETGUARD_AUTH_MODE` without a frontend redeploy.
 */
export function SignIn({
  enabled,
  provider,
  loginUrl,
}: {
  enabled: boolean;
  provider: string;
  loginUrl: string | null;
}) {
  const copy = PROVIDER_COPY[provider] ?? PROVIDER_COPY.github;

  return (
    <div className="signin">
      <div className="panel signin-card">
        <h2>FleetGuard</h2>
        <p className="muted">
          Vehicle defect early warning and recall response. Sign in to view the operator console.
        </p>

        {enabled && loginUrl ? (
          <>
            <a className="signin-btn" href={loginUrl}>
              {copy.mark}
              {copy.label}
            </a>
            <p className="footnote" style={{ marginTop: 16 }}>
              {copy.note}
            </p>
          </>
        ) : (
          <div className="note" style={{ marginTop: 16 }}>
            Sign-in is not configured on this deployment. The <strong>Evidence</strong> page needs
            no session and carries the measured early-warning result.
          </div>
        )}

        <p className="footnote" style={{ marginTop: 14 }}>
          <a href="/#/evidence">View the measured evidence without signing in →</a>
        </p>
      </div>
    </div>
  );
}
