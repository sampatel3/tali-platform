// AnalyticsPage — the dedicated /analytics route.
//
// The page itself now lives in features/analytics/AnalyticsPage.jsx (rebuilt to
// match frontend/public/analytics-preview.html: pulse band + Outcomes / Agent
// fleet / Teaching history / A·B tasks / Decision log). This thin module is the
// stable import target for AppShell's route; it re-exports the real page so the
// routing (and the legacy /reporting alias) keep working unchanged.
export { AnalyticsPage, default } from '../analytics/AnalyticsPage';
